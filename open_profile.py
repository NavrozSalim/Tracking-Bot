"""Open Chrome on one of the Tracking Bot's profiles so you can sign in once.

    python open_profile.py "Amazon AU"            the purchase account: sign in to Amazon
    python open_profile.py "Kogan-TFS" kogan      a Kogan store: sign in to the Kogan dispatch portal
    python open_profile.py "My BigW store" bigw   a Big W store: sign in to the Woolworths MarketPlus portal

The first name is the purchase account's name in the web app (Auto Purchasing), or the store's name for a
marketplace (the bot uploads the tracking there). The profile lives in chrome-profiles/<name> on this PC (or the
folder set for it in tracking.json "profiles"). Sign in to the website only (not to Chrome / Google), tick
"Keep me signed in" / "Remember me", then close the window. The bot uses the same folder.
"""
import json
import subprocess
import sys
from pathlib import Path

from tracker.browser import Account, chrome_exe, keep_session_cookies, launch_args
from tracker.marketplace import BIGW_ORDERS, KOGAN_MANAGE, MARKETPLACE_LABEL

ROOT = Path(__file__).resolve().parent
SITES = {"amazon": ("https://www.amazon.com.au/", "Amazon"), "kogan": (KOGAN_MANAGE, MARKETPLACE_LABEL["kogan"]),
         "bigw": (BIGW_ORDERS, "the Woolworths MarketPlus (Big W) portal"),
         "mydeal": (BIGW_ORDERS, "the Woolworths MarketPlus (Big W) portal")}


def account_for(name, config=ROOT / "tracking.json"):
    own = {}
    if Path(config).exists():
        try:
            own = (json.loads(Path(config).read_text(encoding="utf-8-sig")).get("profiles") or {}).get(name) or {}
        except Exception:
            own = {}
    return Account(name=name, user_data_dir=own.get("user_data_dir", ""),
                   profile_directory=own.get("profile_directory", ""), chrome_path=own.get("chrome_path", ""))


def site_for(arg):
    """(url, what to sign in to) for the optional second argument: amazon (default), kogan, bigw or a URL."""
    arg = (arg or "amazon").strip()
    if arg.lower().startswith("http"):
        return arg, arg
    if arg.lower() not in SITES:
        raise SystemExit(f"Unknown site {arg!r}: use amazon, kogan or bigw.")
    return SITES[arg.lower()]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    acct = account_for(sys.argv[1])
    url, what = site_for(sys.argv[2] if len(sys.argv) > 2 else "")
    folder = acct.profile_dir()
    folder.mkdir(parents=True, exist_ok=True)
    keep_session_cookies(acct)  # the sign-in survives closing this window
    subprocess.Popen([chrome_exe(acct), f"--user-data-dir={folder}", *launch_args(acct), url])
    print(f"Opened '{acct.name}' ({folder}). Sign in to {what} only (tick 'Keep me signed in'), then close the "
          f"window.")


if __name__ == "__main__":
    main()
