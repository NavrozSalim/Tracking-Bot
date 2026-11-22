"""Tracking Bot laptop agent: asks the web app for "tracking" jobs, runs track.py here, sends the results back.

Stand-alone: it needs no order bot on this PC. The web app sends each job's purchased orders (our order id, the
Amazon order ID, the customer's name) and the purchase accounts file; the bot opens those orders in this PC's
Chrome profile for the account (chrome-profiles/<account name>, signed in once with open_profile.bat).

Config: tracking.json (see tracking.example.json) or env TRACKINGBOT_SERVER / TRACKINGBOT_TOKEN.
The token is a web-app ingest token with the "order-bot" scope (the order bot's token works too).
"""
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import requests

from tracker.vpn import DEFAULT_ACCOUNT, DEFAULT_IP, Vpn

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "tracking.json"
KINDS = "tracking"
STDOUT_LIMIT = 20000
LOG_DAYS = 14
EXIT_MEANING = {
    0: "finished",
    1: "stopped (Amazon asks to sign in, the VPN is off, the bot crashed, or Stop was pressed)",
    2: "the orders or accounts file was refused before Chrome opened",
    3: "Chrome did not start on the purchase account's profile",
    4: "the bot hit an unexpected error",
}
SUMMARY_MARKS = (" -> ", "FAILED", "STOPPED", "REJECTED", "Traceback", "Error:", "Laptop agent error")


def summary_lines(text, limit=40):
    return [ln.rstrip() for ln in text.splitlines() if any(m in ln for m in SUMMARY_MARKS)][-limit:]


def load_config(path=CONFIG):
    cfg = {"server": "", "token": "", "poll_seconds": 10, "progress_seconds": 20, "python": sys.executable,
           "vpn_account": DEFAULT_ACCOUNT, "vpn_ip": DEFAULT_IP, "vpncmd": "", "profiles": {}}
    if Path(path).exists():
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    cfg["server"] = (os.environ.get("TRACKINGBOT_SERVER") or cfg["server"]).rstrip("/")
    cfg["token"] = os.environ.get("TRACKINGBOT_TOKEN") or cfg["token"]
    if not cfg["server"] or not cfg["token"] or "your-app.example" in cfg["server"]:
        raise SystemExit(f"Set server and token in {Path(path).name} (copy tracking.example.json to tracking.json).")
    return cfg


def kill_tree(proc):
    """Stop the bot and the Chrome it started (Windows: the whole process tree)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def build_command(python, workdir, job, config=CONFIG):
    return [python, str(ROOT / "track.py"), "--orders", str(workdir / "orders.xlsx"),
            "--accounts-file", str(workdir / "accounts.json"), "--account", job["account"],
            "--output-dir", str(workdir / "output"), "--config", str(config),
            "--marketplace", str(job.get("marketplace") or ""), "--store", str(job.get("store_name") or "")]


class TrackingAgent:
    def __init__(self, cfg, http=requests, sleep=time.sleep, popen=subprocess.Popen, root=ROOT, vpn=None):
        self.cfg, self.http, self.sleep, self.popen, self.root = cfg, http, sleep, popen, Path(root)
        self.vpn = vpn if vpn is not None else Vpn.from_config(cfg)
        self.headers = {"Authorization": f"Bearer {cfg['token']}"}
        self.log_dir = self.root / "logs"

    # ---- window and logs -------------------------------------------------------------------------------

    def say(self, msg="", stamp=True):
        """Print on the agent window with the time, and keep it in logs/agent-<date>.log."""
        now = datetime.now()
        text = "\n".join(f"{now:%H:%M:%S} {ln}" if ln.strip() and stamp else ln for ln in str(msg).split("\n"))
        try:
            print(text, flush=True)
        except Exception:
            pass
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / f"agent-{now:%Y-%m-%d}.log", "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    def prune_logs(self):
        cutoff = datetime.now() - timedelta(days=LOG_DAYS)
        for f in self.log_dir.glob("agent-*.log") if self.log_dir.exists() else []:
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
            except OSError:
                pass

    def echo_output(self, path, pos, final=False):
        """Show the bot's new output lines as they come. Returns the new read position."""
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                data = fh.read()
        except OSError:
            return pos
        if not final:
            cut = data.rfind(b"\n")
            if cut < 0:
                return pos
            data = data[:cut + 1]
        text = data.decode("utf-8", errors="replace").rstrip()
        if text:
            self.say("\n".join("   | " + ln.rstrip() for ln in text.splitlines()), stamp=False)
        return pos + len(data)

    def tell_outcome(self, job, code, text):
        """After a run: the exit code, what it means, and each order's result or the problem."""
        ok = code == 0
        head = (f"==== tracking {job['job_id']} {'FINISHED' if ok else 'STOPPED WITH A PROBLEM'} "
                f"(exit code {code}: {EXIT_MEANING.get(code, 'see the lines above')}) ====")
        lines = summary_lines(text)
        if not ok and not lines:
            lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()][-15:] or ["(the bot printed nothing)"]
        self.say("\n".join([head] + ["   " + ln for ln in lines] + ["=" * min(len(head), 100)]))

    def start_line(self, job):
        return f"tracking {job['job_id']}: {job.get('store_name', '')} / {job['account']} (GET TRACKING)"

    # ---- web app ---------------------------------------------------------------------------------------

    def url(self, path):
        return self.cfg["server"] + path

    def next_job(self):
        r = self.http.get(self.url("/api/v1/ingest/order-bot/next-job/"), headers=self.headers,
                          params={"kinds": KINDS}, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data if data.get("job_id") and data.get("kind") == "tracking" else None

    def download(self, path, dest):
        r = self.http.get(self.url(path), headers=self.headers, timeout=120)
        r.raise_for_status()
        Path(dest).write_bytes(r.content)

    @staticmethod
    def _files(out):
        """Result files read into memory, so the bot can always save its next result while this uploads."""
        files = {}
        for kind, ext in (("done", "xlsx"), ("log", "txt")):
            for p in out.glob(f"*-{kind}.{ext}"):
                try:
                    files[kind] = (p.name, p.read_bytes())
                except OSError:
                    pass
        return files

    def _post(self, path, data, out):
        r = self.http.post(self.url(path), headers=self.headers, data=data, files=self._files(out), timeout=120)
        r.raise_for_status()
        return r.json()

    def _cancelled(self, job):
        try:
            r = self.http.get(self.url(job["status_url"]), headers=self.headers, timeout=30)
            return r.status_code == 404 or r.json().get("status") == "cancelled"
        except Exception:
            return False

    def _complete(self, job, code, text, out):
        for _ in range(5):
            try:
                self._post(job["complete_url"], {"exit_code": code, "stdout": text[-STDOUT_LIMIT:]}, out)
                return
            except Exception:
                self.sleep(10)

    # ---- VPN -------------------------------------------------------------------------------------------

    def connect_vpn(self):
        """Connect the same SoftEther account as the order bot (AU-OLD, public IP 103.100.38.134 by default).
        Nothing changes when it is already connected."""
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

    # ---- jobs ------------------------------------------------------------------------------------------

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
            cmd = build_command(self.cfg["python"], work, job, self.cfg.get("_config_path", CONFIG))
            with open(stdout_path, "w", encoding="utf-8") as so:
                # Unbuffered: each step reaches the file (and this window) as it happens.
                proc = self.popen(cmd, cwd=str(ROOT), stdout=so, stderr=subprocess.STDOUT,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
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
                            kill_tree(proc)
                            break
                code = proc.wait()
            self.echo_output(stdout_path, pos, final=True)
        except Exception as e:
            self.say(f"Laptop agent error: {type(e).__name__}: {e}")
            with open(stdout_path, "a", encoding="utf-8") as fh:
                fh.write(f"Laptop agent error: {type(e).__name__}: {e}\n")
        finally:
            # Customer names and account emails do not stay on the PC.
            (work / "orders.xlsx").unlink(missing_ok=True)
            (work / "accounts.json").unlink(missing_ok=True)
        text = stdout_path.read_text(encoding="utf-8", errors="replace")[-STDOUT_LIMIT:] if stdout_path.exists() else ""
        self.tell_outcome(job, code, text)
        self._complete(job, code, text, out)
        return code

    def run_one(self, job):
        """Run one job; an agent error is shown and sent to the web app instead of closing the agent."""
        self.say("")
        self.say(self.start_line(job))
        try:
            return self.run_job(job)
        except Exception as e:
            self.say(f"AGENT ERROR while running {job.get('job_id')}: {type(e).__name__}: {e}\n"
                     + traceback.format_exc().rstrip())
            out = self.root / "work" / str(job.get("job_id")) / "output"
            try:
                out.mkdir(parents=True, exist_ok=True)
                self._complete(job, 1, f"Laptop agent error: {type(e).__name__}: {e}", out)
            except Exception:
                pass
            return 1

    def loop(self):
        self.prune_logs()
        self.connect_vpn()
        self.say(f"Tracking Bot agent polling {self.cfg['server']} every {self.cfg['poll_seconds']} s "
                 f"(Ctrl+C to stop). This window is also saved in {self.log_dir}")
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


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    try:
        agent = TrackingAgent(load_config())
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
