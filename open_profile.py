"""Open Chrome on the Tracking Bot's profile for a purchase account so you can sign in to Amazon once.

    python open_profile.py "Amazon AU"

The name is the purchase account's name in the web app (Auto Purchasing). The profile lives in
chrome-profiles/<name> on this PC (or the folder set for it in tracking.json "profiles"). Sign in to the website
only (not to Chrome / Google), tick "Keep me signed in", then close the window. The bot uses the same folder.
"""
import json
import subprocess
import sys
from pathlib import Path

from tracker.browser import Account, chrome_exe, keep_session_cookies, launch_args

ROOT = Path(__file__).resolve().parent


def account_for(name, config=ROOT / "tracking.json"):
    own = {}
    if Path(config).exists():
        try:
            own = (json.loads(Path(config).read_text(encoding="utf-8-sig")).get("profiles") or {}).get(name) or {}
        except Exception:
            own = {}
    return Account(name=name, user_data_dir=own.get("user_data_dir", ""),
                   profile_directory=own.get("profile_directory", ""), chrome_path=own.get("chrome_path", ""))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    acct = account_for(sys.argv[1])
    folder = acct.profile_dir()
    folder.mkdir(parents=True, exist_ok=True)
    keep_session_cookies(acct)  # the sign-in survives closing this window
    subprocess.Popen([chrome_exe(acct), f"--user-data-dir={folder}", *launch_args(acct), "https://www.amazon.com.au/"])
    print(f"Opened '{acct.name}' ({folder}). Sign in to Amazon only (tick 'Keep me signed in'), then close the window.")


if __name__ == "__main__":
    main()
