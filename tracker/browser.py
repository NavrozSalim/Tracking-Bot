"""The purchase account's Chrome profile on this PC (the Tracking Bot's own, not the order bot's).

Each account has a profile folder chrome-profiles/<account name> that you sign in to once with
``open_profile.bat "Amazon AU"`` (the bot never logs in). tracking.json may point an account at another folder:

    "profiles": {"Amazon AU": {"user_data_dir": "C:\\\\Users\\\\me\\\\AppData\\\\Local\\\\Google\\\\Chrome\\\\User Data",
                               "profile_directory": "Profile 1"}}

A Chrome already open on the account's debug port is attached to and left open; otherwise the bot starts Chrome
on the folder itself and closes it at the end.
"""
import json
import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "chrome-profiles"
DEFAULT_PORT = 9232


class BrowserStartError(Exception):
    pass


class AccountError(Exception):
    pass


@dataclass
class Account:
    name: str
    vendor: str = ""
    user_data_dir: str = ""
    profile_directory: str = ""
    debug_port: int = DEFAULT_PORT
    chrome_path: str = ""

    def profile_dir(self):
        return Path(self.user_data_dir) if self.user_data_dir else PROFILES / self.name


def load_account(path, name, profiles=None):
    """The account ``name`` from the web app's accounts file ({region: {vendor: [{name, ...}]}}).

    The file's profile paths belong to the order bot's PC, so they are not used here: the profile is this PC's
    chrome-profiles/<name>, or the folder set for the account in tracking.json "profiles"."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise AccountError(f"accounts file could not be read: {type(e).__name__}: {e}") from None
    for vendors in (data or {}).values():
        for vendor, items in (vendors or {}).items():
            for it in items or []:
                if (it.get("name") or "").strip() == name:
                    own = (profiles or {}).get(name) or {}
                    return Account(name=name, vendor=vendor, user_data_dir=own.get("user_data_dir", ""),
                                   profile_directory=own.get("profile_directory", ""),
                                   debug_port=int(own.get("debug_port") or DEFAULT_PORT),
                                   chrome_path=own.get("chrome_path", ""))
    raise AccountError(f"no account named {name!r} in the accounts file")


def chrome_exe(account=None):
    cands = [getattr(account, "chrome_path", ""), os.environ.get("CHROME_PATH", ""), shutil.which("chrome") or "",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise BrowserStartError("Chrome not found; install Google Chrome or set CHROME_PATH")


def _port_up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5).read()
        return True
    except Exception:
        return False


def keep_session_cookies(account):
    """"Continue where you left off", so Chrome keeps the sign-in cookies when the window closes."""
    prefs = account.profile_dir() / (account.profile_directory or "Default") / "Preferences"
    try:
        data = json.loads(prefs.read_text(encoding="utf-8-sig")) if prefs.exists() else {}
    except Exception:
        return
    session = data.setdefault("session", {})
    if session.get("restore_on_startup") == 1:
        return
    session["restore_on_startup"] = 1
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text(json.dumps(data), encoding="utf-8")


def launch_args(account):
    return ["--no-first-run", "--no-default-browser-check",
            f"--profile-directory={account.profile_directory or 'Default'}"]


class BrowserSession:
    def __init__(self, account):
        self.account = account
        self._pw = None
        self._owns = False
        self.context = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        a = self.account
        self._pw = sync_playwright().start()
        try:
            if _port_up(a.debug_port):
                try:
                    browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{a.debug_port}")
                except Exception as e:
                    raise BrowserStartError(
                        f"a Chrome is open on port {a.debug_port} but could not be attached ({type(e).__name__}). "
                        f"Close that Chrome window and run again.") from None
                self.context = browser.contexts[0]
                return self
            pdir = a.profile_dir()
            pdir.mkdir(parents=True, exist_ok=True)
            keep_session_cookies(a)
            try:
                self.context = self._pw.chromium.launch_persistent_context(
                    str(pdir), executable_path=chrome_exe(a), headless=False, no_viewport=True,
                    args=launch_args(a), ignore_default_args=["--enable-automation"])
            except BrowserStartError:
                raise
            except Exception as e:
                raise BrowserStartError(
                    f"could not start Chrome on profile {pdir} ({type(e).__name__}: {str(e)[:200]}). "
                    f"Close any Chrome window using this profile and try again.") from None
            self._owns = True
            return self
        except BaseException:
            self._pw.stop()
            self._pw = None
            raise

    def __exit__(self, *exc):
        try:
            if self._owns and self.context is not None:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    def new_page(self):
        page = self.context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(60000)
        return page
