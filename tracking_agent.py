"""Tracking Bot laptop agent: asks the web app for "tracking" jobs, runs track.py here, sends the results back.

It is the order bot's laptop agent (same window, logs, VPN check and Stop handling) with its own job kind, so
both agents can run side by side on the laptop. The web app never gives this agent a job while the order bot
is using the same purchase account's Chrome profile, and the other way round.

Config: tracking.json (see tracking.example.json). Server and token come from the order bot's agent.json.
"""
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from tracker.orderbot import use_order_bot

ROOT = Path(__file__).resolve().parent
KINDS = "tracking"
EXIT_MEANING = {
    0: "finished",
    1: "stopped (Amazon asks to sign in, the bot crashed, or Stop was pressed)",
    2: "the orders or accounts file was refused before Chrome opened",
    3: "Chrome did not start on the purchase account's profile",
    4: "the bot hit an unexpected error",
}


def load_tracking_config(path=ROOT / "tracking.json"):
    cfg = {"order_bot_dir": "", "poll_seconds": 10}
    if Path(path).exists():
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    return cfg


def build_command(python, workdir, job, order_bot_dir):
    return [python, str(ROOT / "track.py"), "--orders", str(workdir / "orders.xlsx"),
            "--accounts-file", str(workdir / "accounts.json"), "--account", job["account"],
            "--output-dir", str(workdir / "output"), "--order-bot-dir", str(order_bot_dir)]


def make_agent_class(order_agent):
    """The tracking agent built on the order bot's agent module (``order_agent``)."""

    class TrackingAgent(order_agent.Agent):
        order_bot_dir = ""

        def next_job(self):
            r = self.http.get(self.url("/api/v1/ingest/order-bot/next-job/"), headers=self.headers,
                              params={"kinds": KINDS}, timeout=30)
            r.raise_for_status()
            data = r.json()
            return data if data.get("job_id") and data.get("kind") == "tracking" else None

        def tell_outcome(self, job, code, text):
            """After a run: the exit code, what it means, and each order's result or the problem."""
            ok = code == 0
            head = (f"==== tracking {job['job_id']} {'FINISHED' if ok else 'STOPPED WITH A PROBLEM'} "
                    f"(exit code {code}: {EXIT_MEANING.get(code, 'see the lines above')}) ====")
            lines = order_agent.summary_lines(text)
            if not ok and not lines:
                lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()][-15:] or ["(the bot printed nothing)"]
            self.say("\n".join([head] + ["   " + ln for ln in lines] + ["=" * min(len(head), 100)]))

        def start_line(self, job):
            return f"tracking {job['job_id']}: {job.get('store_name', '')} / {job['account']} (GET TRACKING)"

        def run_job(self, job):
            refused = self.refuse_off_vpn(job)
            if refused is not None:
                return refused
            work = self.root / "work" / job["job_id"]
            out = work / "output"
            out.mkdir(parents=True, exist_ok=True)
            stdout_path = work / "stdout.txt"
            code = 1
            try:
                for kind, name in (("orders", "orders.xlsx"), ("accounts", "accounts.json")):
                    self.download(job["files"][kind], work / name)
                cmd = build_command(self.cfg["python"], work, job, self.order_bot_dir)
                with open(stdout_path, "w", encoding="utf-8") as so:
                    proc = self.popen(cmd, cwd=str(ROOT), stdout=so, stderr=subprocess.STDOUT,
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8",
                                           "PYTHONUNBUFFERED": "1"})
                    last = last_cancel = 0.0
                    pos = 0
                    while proc.poll() is None:
                        self.sleep(self.cfg.get("tick_seconds", 1))
                        pos = self.echo_output(stdout_path, pos)
                        if time.monotonic() - last >= self.cfg["progress_seconds"]:
                            last = time.monotonic()
                            try:
                                self._post(job["progress_url"], {}, out)
                            except Exception:
                                pass
                        if time.monotonic() - last_cancel >= self.cfg.get("cancel_seconds", 5):
                            last_cancel = time.monotonic()
                            if self._cancelled(job):
                                self.say(f"tracking {job['job_id']}: stopped from the web app")
                                order_agent._kill_tree(proc)
                                break
                    code = proc.wait()
                self.echo_output(stdout_path, pos, final=True)
            except Exception as e:
                self.say(f"Laptop agent error: {type(e).__name__}: {e}")
                with open(stdout_path, "a", encoding="utf-8") as fh:
                    fh.write(f"Laptop agent error: {type(e).__name__}: {e}\n")
            finally:
                # Customer names and account emails do not stay on the laptop.
                (work / "orders.xlsx").unlink(missing_ok=True)
                (work / "accounts.json").unlink(missing_ok=True)
            text = (stdout_path.read_text(encoding="utf-8", errors="replace")[-order_agent.STDOUT_LIMIT:]
                    if stdout_path.exists() else "")
            self.tell_outcome(job, code, text)
            self._complete(job, code, text, out)
            return code

        def connect_vpn(self):
            """Connect the same SoftEther account as the order bot (vpn_account / vpn_ip from its agent.json).
            Nothing changes when it is already connected (e.g. by the order bot agent)."""
            if self.vpn.enabled:
                self.say(f"Connecting SoftEther VPN {self.vpn.account} (expect public IP {self.vpn.ip})...")
            ok, msg = self.vpn.connect()
            self.say(("  " if ok else "  WARNING: ") + msg
                     + ("" if ok else " Tracking jobs will be stopped until it is connected."))
            return ok

        def refuse_off_vpn(self, job):
            """Before a job: on the VPN's IP, or connect it again; stop the job when it still is not."""
            ok, msg = self.vpn.check()
            if ok:
                return None
            self.say(f"  {msg} Connecting it again before the job...")
            if self.connect_vpn():
                return None
            ok, msg = self.vpn.check()
            text = (f"{msg} No order was opened. Connect {self.vpn.account} in SoftEther VPN Client "
                    f"(or restart the Tracking Bot agent) and press Get tracking again.")
            self.say(f"  {text}")
            out = self.root / "work" / job["job_id"] / "output"
            out.mkdir(parents=True, exist_ok=True)
            self._complete(job, 1, text, out)
            return 1

        def loop(self):
            self.prune_logs()
            self.connect_vpn()
            self.say(f"Tracking Bot agent polling {self.cfg['server']} every {self.cfg['poll_seconds']} s "
                     f"(Ctrl+C to stop). Order bot: {self.order_bot_dir}. This window is also saved in {self.log_dir}")
            failing = False
            while True:
                try:
                    job = self.next_job()
                    if failing:
                        self.say("server reachable again")
                    failing = False
                except Exception as e:
                    if not failing:
                        self.say(f"cannot reach the server (will keep trying): {type(e).__name__}: {e}")
                    failing = True
                    job = None
                if job:
                    self.run_one(job)
                    self.say("waiting for the next tracking job...")
                else:
                    self.sleep(self.cfg["poll_seconds"])

    return TrackingAgent


def build_agent(tracking_cfg=None, **kw):
    tcfg = tracking_cfg if tracking_cfg is not None else load_tracking_config()
    order_dir = use_order_bot(tcfg.get("order_bot_dir", ""))
    import agent as order_agent  # the order bot's agent.py

    cfg = order_agent.load_config(order_dir / "agent.json")
    cfg.update({k: v for k, v in tcfg.items() if k != "order_bot_dir"})
    cfg["python"] = tcfg.get("python") or sys.executable
    cls = make_agent_class(order_agent)
    agent = cls(cfg, root=ROOT, **kw)
    agent.order_bot_dir = str(order_dir)
    return agent


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    try:
        agent = build_agent()
    except SystemExit as e:
        print(f"TRACKING AGENT DID NOT START: {e}")
        return 1
    try:
        agent.loop()
    except KeyboardInterrupt:
        agent.say("Tracking agent stopped (Ctrl+C).")
        return 0
    except BaseException as e:
        agent.say("\n".join(["=" * 70, f"TRACKING AGENT CRASHED: {type(e).__name__}: {e}",
                             traceback.format_exc().rstrip(), "=" * 70]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
