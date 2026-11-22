"""The tracking agent: takes only tracking jobs, runs track.py, sends the result file and its window output."""
import json
from pathlib import Path

import tracking_agent


class Resp:
    def __init__(self, data=None, content=b"", status=200):
        self._data, self.content, self.status_code = data or {}, content, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


JOB = {"job_id": "j1", "kind": "tracking", "account": "Amazon AU", "store_name": "Kogan-TFS",
       "status_url": "/s/", "progress_url": "/p/", "complete_url": "/c/",
       "files": {"orders": "/f/orders/", "mapping": "/f/mapping/", "accounts": "/f/accounts/"}}


class Http:
    def __init__(self, job=JOB):
        self.job, self.posts, self.gets = dict(job), [], []

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets.append((url, params))
        if url.endswith("/next-job/"):
            job, self.job = self.job, {"job_id": None}
            return Resp(job)
        if url.endswith("/s/"):
            return Resp({"status": "running"})
        return Resp(content=b"file:" + url.encode())

    def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.posts.append((url, data, files))
        return Resp({"status": "finished"})


class Vpn:
    """on: connected now; connects: whether connect() gets it connected."""
    account, ip = "AU-OLD", "103.100.38.134"

    def __init__(self, on=True, connects=True, enabled=True):
        self.on, self.connects, self.enabled, self.connect_calls = on, connects, enabled, 0

    def check(self):
        return (True, "VPN AU-OLD is on") if self.on else (False, "VPN AU-OLD is not connected: IP 1.2.3.4")

    def connect(self):
        self.connect_calls += 1
        self.on = self.on or self.connects
        return self.check()


class Proc:
    def __init__(self, cmd, cwd=None, stdout=None, stderr=None, env=None):
        self.cmd, self.env = cmd, env
        out = Path(cmd[cmd.index("--output-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "tracking-done.xlsx").write_bytes(b"xlsx")
        (out / "tracking-log.txt").write_text("log", encoding="utf-8")
        stdout.write("2026-11-20 10:00:00   K1 503-1 -> FOUND TB1 Amazon\n")
        stdout.flush()
        Proc.last = self

    def poll(self):
        return 0

    def wait(self):
        return 0


CFG = {"server": "https://app.example.net", "token": "tok", "poll_seconds": 1, "progress_seconds": 20,
       "python": "python", "vpn_account": "AU-OLD", "vpn_ip": "103.100.38.134", "vpncmd": "", "profiles": {}}


def make(tmp_path, monkeypatch, http, vpn=None):
    monkeypatch.setattr(tracking_agent, "ROOT", tmp_path)
    return tracking_agent.TrackingAgent(dict(CFG), http=http, sleep=lambda s: None, popen=Proc, root=tmp_path,
                                        vpn=vpn or Vpn())


def test_takes_only_tracking_jobs(tmp_path, monkeypatch):
    http = Http({**JOB, "kind": "purchase"})
    agent = make(tmp_path, monkeypatch, http)
    assert agent.next_job() is None
    assert http.gets[0][1] == {"kinds": "tracking,upload_tracking"}


def test_runs_track_and_sends_the_results(tmp_path, monkeypatch, capsys):
    http = Http()
    agent = make(tmp_path, monkeypatch, http)
    job = agent.next_job()
    assert agent.run_one(job) == 0
    cmd = Proc.last.cmd
    assert cmd[1].endswith("track.py") and "--config" in cmd and cmd[cmd.index("--account") + 1] == "Amazon AU"
    assert Proc.last.env["PYTHONUNBUFFERED"] == "1"
    url, data, files = http.posts[-1]
    assert url.endswith("/c/") and data["exit_code"] == 0 and "FOUND TB1" in data["stdout"]
    assert set(files) == {"done", "log"}
    # No mapping download; customer names and account emails are removed after the run.
    assert not any(u.endswith("/f/mapping/") for u, _ in http.gets)
    assert not (tmp_path / "work" / "j1" / "orders.xlsx").exists()
    shown = capsys.readouterr().out
    assert "GET TRACKING" in shown and "FINISHED (exit code 0: finished)" in shown and "FOUND TB1" in shown
    assert list((tmp_path / "logs").glob("agent-*.log"))


def test_example_config_is_valid_json_and_must_be_filled_in(tmp_path):
    example = Path(__file__).resolve().parent.parent / "tracking.example.json"
    cfg = json.loads(example.read_text())
    assert (cfg["vpn_account"], cfg["vpn_ip"]) == ("AU-OLD", "103.100.38.134")
    try:
        tracking_agent.load_config(example)
    except SystemExit as e:
        assert "Set server and token" in str(e)
    else:
        raise AssertionError("the example server must be refused")
    (tmp_path / "tracking.json").write_text(json.dumps({**cfg, "server": "https://app.example.net/", "token": "t"}))
    got = tracking_agent.load_config(tmp_path / "tracking.json")
    assert got["server"] == "https://app.example.net" and got["poll_seconds"] == 10


def test_connects_the_order_bots_vpn_at_start(tmp_path, monkeypatch, capsys):
    vpn = Vpn(on=False)
    agent = make(tmp_path, monkeypatch, Http(), vpn)
    assert agent.connect_vpn() is True and vpn.connect_calls == 1
    out = capsys.readouterr().out
    assert "Connecting SoftEther VPN AU-OLD (expect public IP 103.100.38.134)" in out and "is on" in out


def test_reconnects_before_a_job_when_the_vpn_dropped(tmp_path, monkeypatch):
    vpn = Vpn(on=False)
    http = Http()
    agent = make(tmp_path, monkeypatch, http, vpn)
    assert agent.run_one(agent.next_job()) == 0
    assert vpn.connect_calls == 1 and Proc.last is not None


def test_job_stopped_when_the_vpn_cannot_connect(tmp_path, monkeypatch):
    Proc.last = None
    vpn = Vpn(on=False, connects=False)
    http = Http()
    agent = make(tmp_path, monkeypatch, http, vpn)
    assert agent.run_one(agent.next_job()) == 1
    url, data, _ = http.posts[-1]
    assert url.endswith("/c/") and data["exit_code"] == 1 and "No order was opened" in data["stdout"]
    assert Proc.last is None  # the bot never started


def test_same_vpn_name_and_ip_as_the_order_bot(tmp_path):
    from tracker.vpn import DEFAULT_ACCOUNT, DEFAULT_IP
    agent = tracking_agent.TrackingAgent(dict(CFG), http=Http(), sleep=lambda s: None, popen=Proc, root=tmp_path)
    assert (agent.vpn.account, agent.vpn.ip) == ("AU-OLD", "103.100.38.134") == (DEFAULT_ACCOUNT, DEFAULT_IP)


def test_profile_is_this_pcs_own_folder(tmp_path):
    from tracker.browser import PROFILES, load_account
    (tmp_path / "accounts.json").write_text(json.dumps({"au": {"amazon_au": [
        {"name": "Amazon AU", "chrome_profile": "P1", "user_data_dir": "C:/OrderBot/elsewhere", "debug_port": 9222}]}}))
    acct = load_account(tmp_path / "accounts.json", "Amazon AU")
    assert acct.vendor == "amazon_au" and acct.profile_dir() == PROFILES / "Amazon AU"
    acct = load_account(tmp_path / "accounts.json", "Amazon AU",
                        {"Amazon AU": {"user_data_dir": "D:/Chrome", "profile_directory": "Profile 1"}})
    assert (str(acct.profile_dir()), acct.profile_directory) == (str(Path("D:/Chrome")), "Profile 1")
