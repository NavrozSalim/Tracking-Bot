"""Tracking Bot run: read the vendor tracking of every order in the orders file. Started by tracking_agent.py.

    python track.py --orders orders.xlsx --accounts-file accounts.json --account "Amazon AU" --output-dir out

Exit codes: 0 finished, 1 stopped (Amazon asks to sign in), 2 file / account refused, 3 Chrome did not start,
4 unexpected error. Results: <output-dir>/tracking-done.xlsx (one row per package), tracking-log.txt.
Nothing is bought or changed on the vendor's site: the bot only opens orders and their tracking pages.
"""
import argparse
import logging
import sys
import traceback
from pathlib import Path

import json

from tracker.amazon import AmazonTracker, Package, SignInNeeded
from tracker.browser import AccountError, BrowserSession, BrowserStartError, load_account
from tracker.results import OrdersFileError, ResultFile, read_orders, split_numbers

log = logging.getLogger("trackingbot")
# Purchase-account vendors the Tracking Bot can read, and their site.
VENDOR_BASE = {"amazon_au": "https://www.amazon.com.au"}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--orders", required=True)
    ap.add_argument("--accounts-file", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--config", default="", help="tracking.json (its \"profiles\" set an account's Chrome folder)")
    return ap.parse_args(argv)


def setup_logging(out):
    out.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(out / "tracking-log.txt", encoding="utf-8")]
    for h in handlers:
        h.setFormatter(fmt)
    lg = logging.getLogger("trackingbot")
    lg.handlers[:] = handlers
    lg.setLevel(logging.INFO)
    lg.propagate = False


def _where(e):
    tb = traceback.extract_tb(e.__traceback__)
    if not tb:
        return ""
    f = tb[-1]
    return f"{Path(f.filename).name}:{f.lineno} in {f.name}"


def _profiles(config):
    try:
        return json.loads(Path(config).read_text(encoding="utf-8-sig")).get("profiles") or {} if config else {}
    except Exception:
        return {}


def run(args, session_factory=None, helpers=None):
    if helpers is None:
        import tracker.page as helpers
    out = Path(args.output_dir)
    results = ResultFile(out / "tracking-done.xlsx")
    try:
        orders = read_orders(args.orders)
    except OrdersFileError as e:
        log.error("REJECTED: %s", e)
        return 2
    try:
        account = load_account(args.accounts_file, args.account, _profiles(getattr(args, "config", "")))
    except AccountError as e:
        log.error("REJECTED: %s", e)
        return 2
    base = VENDOR_BASE.get(account.vendor)
    if not base:
        log.error("REJECTED: the Tracking Bot does not read %s orders yet", account.vendor)
        return 2
    log.info("TRACKING: %d order(s) on %s (%s), Chrome profile %s", len(orders), account.name, account.vendor,
             account.profile_dir())
    tracker = AmazonTracker(base, helpers)
    stop_reason = ""
    try:
        with (session_factory or BrowserSession)(account) as session:
            page = session.new_page()
            for i, order in enumerate(orders, 1):
                numbers = split_numbers(order.get("vendor_order_number"))
                log.info("---- order %d of %d: %s (vendor order %s)", i, len(orders), order.get("invoice") or "?",
                         ", ".join(numbers) or "none")
                if not numbers:
                    results.add(order, "", Package(status="error", error="no vendor order number"))
                for number in numbers:
                    if stop_reason:
                        pkgs = [Package(status="error", error=stop_reason)]
                    else:
                        try:
                            pkgs = tracker.read_order(page, number, order.get("customer_name", ""))
                        except SignInNeeded as e:
                            stop_reason = str(e)
                            log.error("STOPPED: %s", stop_reason)
                            pkgs = [Package(status="error", error=stop_reason)]
                        except Exception as e:
                            log.error("    FAILED reading order %s: %s: %s  [%s]", number, type(e).__name__,
                                      str(e)[:300], _where(e))
                            pkgs = [Package(status="error",
                                            error=f"order {number}: {type(e).__name__}: {str(e)[:200]}")]
                    for p in pkgs:
                        results.add(order, number, p)
                        log.info("  %s %s -> %s %s %s", order.get("invoice") or "?", number, p.get("status", "").upper(),
                                 p.get("tracking_number", ""), p.get("carrier", "") or p.get("error", ""))
                results.save()
    except BrowserStartError as e:
        log.error("STOPPED: Chrome did not start: %s", e)
        results.save()
        return 3
    except Exception as e:
        log.error("STOPPED: unexpected error %s: %s  [%s]\n%s", type(e).__name__, e, _where(e),
                  traceback.format_exc().rstrip())
        results.save()
        return 4
    results.save()
    found = sum(1 for r in results.rows if r.get("status") == "found")
    log.info("run finished: %d tracking number(s) read for %d order(s)", found, len(orders))
    return 1 if stop_reason else 0


def main(argv=None):
    args = parse_args(argv)
    setup_logging(Path(args.output_dir))
    try:
        code = run(args)
    except SystemExit as e:
        log.error("REJECTED: %s", e)
        code = 2
    log.info("run finished, exit code %d", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
