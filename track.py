"""Tracking Bot run: read the vendor tracking of every order in the orders file. Started by tracking_agent.py.

    python track.py --orders orders.xlsx --accounts-file accounts.json --account "Amazon AU" --output-dir out

Exit codes: 0 finished, 1 stopped (Amazon asks to sign in), 2 file / account refused, 3 Chrome did not start,
4 unexpected error. Results: <output-dir>/tracking-done.xlsx (one row per package), tracking-log.txt.
Nothing is bought or changed on the vendor's site: the bot only opens orders and their tracking pages.

With --marketplace kogan / bigw the tracking read is then uploaded to the marketplace order (the orders file's
``invoice`` column) in the store's Chrome profile (--store), and the order is searched again to check it
(tracker/marketplace.py). tracking.json "upload_tracking": false turns that off, "upload_dry_run": true fills the
form without saving.
"""
import argparse
import logging
import sys
import traceback
from pathlib import Path

import json

from tracker.amazon import AmazonTracker, Package, SignInNeeded
from tracker.costway import CostwayTracker
from tracker.browser import Account, AccountError, BrowserSession, BrowserStartError, load_account
from tracker.marketplace import (MARKETPLACE_LABEL, PORTALS, MarketplaceSignIn, UploadError, choose_uploads,
                                 courier_for)
from tracker.results import OrdersFileError, ResultFile, read_orders, split_numbers

log = logging.getLogger("trackingbot")
# Purchase-account vendors the Tracking Bot can read, and their site.
VENDOR_BASE = {"amazon_au": "https://www.amazon.com.au", "costway_au": "https://au.costway.com"}
TRACKERS = {"amazon_au": AmazonTracker, "costway_au": CostwayTracker}
# The marketplace's Chrome profile when tracking.json "profiles" sets no debug_port for it (not the Amazon one).
MARKETPLACE_PORT = 9233


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--orders", required=True)
    ap.add_argument("--accounts-file", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--config", default="", help="tracking.json (its \"profiles\" set an account's Chrome folder)")
    ap.add_argument("--marketplace", default="", help="kogan / bigw: upload the tracking to this marketplace")
    ap.add_argument("--store", default="", help="store name: its Chrome profile is signed in to the marketplace")
    ap.add_argument("--upload-only", action="store_true",
                    help="upload the orders file's tracking_number to the marketplace (Amazon is not opened)")
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


def _config(config):
    try:
        return (json.loads(Path(config).read_text(encoding="utf-8-sig")) or {}) if config else {}
    except Exception:
        return {}


def _profiles(config):
    return _config(config).get("profiles") or {}


def marketplace_account(store, marketplace, profiles):
    """The store's seller-portal Chrome profile: chrome-profiles/<store name>, or the folder in "profiles"."""
    name = (store or "").strip() or MARKETPLACE_LABEL.get(marketplace, marketplace)
    own = (profiles or {}).get(name) or {}
    return Account(name=name, vendor=marketplace, user_data_dir=own.get("user_data_dir", ""),
                   profile_directory=own.get("profile_directory", ""),
                   debug_port=int(own.get("debug_port") or MARKETPLACE_PORT), chrome_path=own.get("chrome_path", ""))


def plan_uploads(marketplace, cfg, orders, results):
    """(order, row, marketplace order ID, courier) for each order to upload; the rows not uploaded get
    upload_status / upload_note saying why."""
    label = MARKETPLACE_LABEL.get(marketplace, marketplace)
    todo = []
    for order in orders:
        rows = [r for r in results.rows if r.get("order_ref") == order.get("order_ref")]
        row = choose_uploads(rows)
        mp_order = (order.get("invoice") or "").strip()
        if row is not None:
            # The web app sends the courier the user saved for the carrier; older jobs fall back to tracking.json.
            courier = (order.get("courier") or "").strip() or courier_for(
                marketplace, row.get("carrier", ""), cfg.get("couriers"), row.get("tracking_number"))
            if not mp_order:
                row["upload_status"], row["upload_note"] = "failed", f"no {label} order ID for this order"
            elif not courier:
                row["upload_status"], row["upload_note"] = "failed", (
                    f"no {label} courier for carrier '{row.get('carrier') or '?'}': add it to \"couriers\" in "
                    f"tracking.json")
            else:
                todo.append((order, row, mp_order, courier))
        for r in rows:
            if r.get("upload_status"):
                log.info("  %s %s -> UPLOAD %s %s", mp_order or "?", r.get("tracking_number", ""),
                         r["upload_status"].upper(), r.get("upload_note", ""))
    return todo


def upload_tracking(args, cfg, orders, results, helpers, session_factory=None):
    """Upload each order's tracking to the marketplace and check it there (tracker/marketplace.py). Sets
    upload_status / upload_note on the result rows. Returns the stop reason ('' when the uploads ran)."""
    marketplace = (getattr(args, "marketplace", "") or "").strip().lower()
    if not marketplace or marketplace == "tracking":  # "tracking": the store's marketplace is not a bot one
        return ""
    if cfg.get("upload_tracking", True) is False:
        log.info("UPLOAD: off (upload_tracking is false in tracking.json)")
        return ""
    portal_cls = PORTALS.get(marketplace)
    if portal_cls is None:
        log.info("UPLOAD: the bot does not upload tracking to %s", marketplace)
        return ""
    label = MARKETPLACE_LABEL.get(marketplace, marketplace)
    todo = plan_uploads(marketplace, cfg, orders, results)
    results.save()
    if not todo:
        log.info("UPLOAD: no tracking to upload to %s", label)
        return ""
    dry = bool(cfg.get("upload_dry_run"))
    account = marketplace_account(getattr(args, "store", ""), marketplace, cfg.get("profiles"))
    log.info("UPLOAD: %d tracking number(s) to %s%s, Chrome profile %s", len(todo), label,
             " (DRY RUN: nothing is saved)" if dry else "", account.profile_dir())
    stop = ""
    try:
        with (session_factory or BrowserSession)(account) as session:
            portal = portal_cls(session.new_page(), helpers, dry_run=dry, shots=Path(args.output_dir))
            for i, (order, row, mp_order, courier) in enumerate(todo, 1):
                number = row.get("tracking_number", "")
                if stop:
                    row["upload_status"], row["upload_note"] = "failed", stop
                    continue
                log.info("---- upload %d of %d: %s order %s <- %s (%s)", i, len(todo), label, mp_order, number,
                         courier)
                try:
                    status, note = portal.upload(mp_order, number, courier)
                except MarketplaceSignIn as e:
                    stop = str(e)
                    log.error("UPLOAD STOPPED: %s", stop)
                    status, note = "failed", stop
                except Exception as e:
                    shot = portal.screenshot(mp_order)
                    kind = "" if isinstance(e, UploadError) else f"{type(e).__name__}: "
                    status, note = "failed", f"{kind}{str(e)[:250]}" + (f" (screenshot {shot})" if shot else "")
                    log.error("    FAILED uploading %s to %s order %s: %s  [%s]", number, label, mp_order, note,
                              _where(e))
                row["upload_status"], row["upload_note"] = status, note
                log.info("  %s %s -> UPLOAD %s %s", mp_order, number, status.upper(), note)
                results.save()
    except BrowserStartError as e:
        stop = f"Chrome did not start on the {label} profile: {e}"
        log.error("UPLOAD STOPPED: %s", stop)
        for _, row, _, _ in todo:
            if not row.get("upload_status"):
                row["upload_status"], row["upload_note"] = "failed", stop
    results.save()
    done = sum(1 for _, r, _, _ in todo if r.get("upload_status") in ("verified", "already"))
    log.info("upload finished: %d of %d tracking number(s) on %s", done, len(todo), label)
    return stop


def run_upload_only(args, orders, results, helpers, session_factory=None):
    """The Upload button on the Orders page: upload the tracking numbers in the orders file (``tracking_number``,
    ``carrier`` columns) to the marketplace and check them. Amazon is not opened."""
    marketplace = (getattr(args, "marketplace", "") or "").strip().lower()
    for order in orders:
        results.add(order, order.get("vendor_order_number", ""),
                    Package(status="found", tracking_number=(order.get("tracking_number") or "").strip(),
                            carrier=order.get("carrier", "")))
    if marketplace not in PORTALS:
        log.error("REJECTED: the Tracking Bot does not upload tracking to %r", marketplace or "(no marketplace)")
        for r in results.rows:
            r["upload_status"], r["upload_note"] = "failed", f"the bot does not upload to {marketplace or '?'}"
        results.save()
        return 2
    log.info("UPLOAD ONLY: %d tracking number(s) to %s", len(orders), MARKETPLACE_LABEL[marketplace])
    for r in results.rows:
        if not r.get("tracking_number"):
            r["status"], r["upload_status"], r["upload_note"] = "error", "failed", "no tracking number to upload"
    cfg = {**_config(getattr(args, "config", "")), "upload_tracking": True}  # the button was pressed: upload
    stop = upload_tracking(args, cfg, orders, results, helpers, session_factory)
    results.save()
    return 1 if stop else 0


def run(args, session_factory=None, helpers=None, upload_session_factory=None):
    if helpers is None:
        import tracker.page as helpers
    out = Path(args.output_dir)
    results = ResultFile(out / "tracking-done.xlsx")
    try:
        orders = read_orders(args.orders)
    except OrdersFileError as e:
        log.error("REJECTED: %s", e)
        return 2
    if getattr(args, "upload_only", False):
        return run_upload_only(args, orders, results, helpers, upload_session_factory)
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
    tracker = TRACKERS[account.vendor](base, helpers)
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
    # Reading only: the tracking is uploaded when the user presses Upload tracking on the Orders page (an
    # upload_tracking job, --upload-only).
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
