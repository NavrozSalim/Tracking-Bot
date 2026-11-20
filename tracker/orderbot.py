"""The Tracking Bot uses the order bot installed on the same laptop for its Chrome profiles (the purchase
accounts are signed in there), its laptop agent window, the VPN check and its page helpers.

It finds the order bot folder from ``order_bot_dir`` in tracking.json, the ORDER_BOT_DIR environment variable,
C:\\OrderBot, or the "Order bot" folder next to this one.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIRS = (r"C:\OrderBot", str(ROOT.parent / "Order bot"))


def find_order_bot(configured=""):
    for cand in (configured, os.environ.get("ORDER_BOT_DIR", ""), *DEFAULT_DIRS):
        if cand and (Path(cand) / "core" / "browser.py").exists() and (Path(cand) / "agent.py").exists():
            return Path(cand).resolve()
    raise SystemExit("Order bot folder not found. Set order_bot_dir in tracking.json (e.g. C:\\OrderBot).")


def use_order_bot(configured=""):
    """Put the order bot folder on the import path; returns it."""
    path = find_order_bot(configured)
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return path
