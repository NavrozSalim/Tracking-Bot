"""track.py end to end in a real Chrome: Amazon's order and Track package pages are answered with local copies,
so the bot opens each order, clicks every Track package button and reads the tracking as on the laptop."""
import json
from types import SimpleNamespace

import openpyxl
import pytest

pw = pytest.importorskip("playwright.sync_api")

import tracker.page as base  # noqa: E402

import track  # noqa: E402

AMZ = "https://www.amazon.com.au"
TWO_PACKAGES = "503-2272946-3274202"
NOT_SHIPPED = "503-0000000-0000001"
UNKNOWN = "503-0000000-0000002"
SIGNED_OUT = "503-0000000-0000003"
CANCELLED = "503-0000000-0000004"


def order_page(number, packages):
    boxes = "".join(
        f"<div class='shipment'><h3>Arriving Tuesday</h3><span class='a-button'><span class='a-button-inner'>"
        f"<a class='a-button-text' href='/progress-tracker/package?orderId={number}&shipmentId={p}'>Track package</a>"
        f"</span></span><a href='#'>View or edit order</a></div>" for p in packages)
    return f"<html><body><h1>Order Details</h1><p>Order# {number}</p>{boxes}</body></html>"


TRACK_A = """<html><body><div>Ordered</div><div>Shipped</div><div>Out for delivery</div><div>Delivered</div>
<div class='box'><h4>Delivery By Amazon</h4><div>Tracking ID: TBAU316654872</div><a href='#'>See all updates</a></div>
<div class='box'><h4>Shipping Address</h4><div>Elena Legkova</div><div>U 22 21 Lacey Rd</div>
<div>CARSELDINE, QLD 4034</div></div></body></html>"""
TRACK_B = """<html><body><div>Ordered</div><div>Preparing for dispatch</div>
<div class='box'><h4>Shipping Address</h4><div>Elena Legkova</div></div></body></html>"""
PAGES = {
    f"order-details?orderID={TWO_PACKAGES}": order_page(TWO_PACKAGES, ["A", "B"]),
    f"order-details?orderID={NOT_SHIPPED}": order_page(NOT_SHIPPED, []).replace("Order Details", "Not yet dispatched"),
    f"order-details?orderID={UNKNOWN}": "<html><body>We're unable to load your order. Try again later.</body></html>",
    f"order-details?orderID={SIGNED_OUT}": "<html><body><script>location.replace('/ap/signin?x=1')</script></body></html>",
    f"order-details?orderID={CANCELLED}": "<html><body><h1>Order Details</h1><p>This order has been cancelled.</p></body></html>",
    "shipmentId=A": TRACK_A,
    "shipmentId=B": TRACK_B,
    "/ap/signin": "<html><body><h1>Sign in</h1><input id='ap_email'></body></html>",
}


@pytest.fixture(scope="module")
def browser():
    p = pw.sync_playwright().start()
    try:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = p.chromium.launch(headless=True)
    except Exception as e:
        p.stop()
        pytest.skip(f"no browser for Playwright: {e}")
    yield b
    b.close()
    p.stop()


def fast_settle(page, ms=800):
    try:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(200)
    except Exception:
        pass


@pytest.fixture
def session_factory(browser, monkeypatch):
    monkeypatch.setattr(base, "settle", fast_settle)
    monkeypatch.setattr(base, "PAUSE_MIN_MS", 1)
    monkeypatch.setattr(base, "PAUSE_MAX_MS", 2)
    keys = sorted(PAGES, key=len, reverse=True)

    def handler(route):
        url = route.request.url
        for k in keys:
            if k in url:
                return route.fulfill(status=200, content_type="text/html", body=PAGES[k])
        return route.fulfill(status=404, content_type="text/html", body="<html><body>not here</body></html>")

    class Session:
        def __init__(self, account):
            self.account = account

        def __enter__(self):
            self.ctx = browser.new_context()
            self.ctx.route(f"{AMZ}/**", handler)
            return self

        def __exit__(self, *exc):
            self.ctx.close()

        def new_page(self):
            pg = self.ctx.new_page()
            pg.set_default_timeout(5000)
            return pg

    return Session


def make_run(tmp_path, orders):
    wb = openpyxl.Workbook()
    wb.active.append(["order_ref", "invoice", "vendor", "vendor_order_number", "customer_name", "purchased_at"])
    for o in orders:
        wb.active.append(o)
    wb.save(tmp_path / "orders.xlsx")
    (tmp_path / "accounts.json").write_text(json.dumps(
        {"au": {"amazon_au": [{"name": "Amazon AU", "chrome_profile": "Amazon AU", "debug_port": 9999}]}}))
    return SimpleNamespace(orders=str(tmp_path / "orders.xlsx"), accounts_file=str(tmp_path / "accounts.json"),
                           account="Amazon AU", output_dir=str(tmp_path / "out"), config="")


def results(tmp_path):
    rows = list(openpyxl.load_workbook(tmp_path / "out" / "tracking-done.xlsx").active.iter_rows(values_only=True))
    return [{k: ("" if v is None else v) for k, v in zip(rows[0], r)} for r in rows[1:]]


def test_reads_every_package_and_each_kind_of_order(tmp_path, session_factory):
    args = make_run(tmp_path, [
        ["u1", "K1", "amazon_au", TWO_PACKAGES, "Elena Legkova", ""],
        ["u2", "K2", "amazon_au", NOT_SHIPPED, "Jason Oakley", ""],
        ["u3", "K3", "amazon_au", UNKNOWN, "Jason Oakley", ""],
        ["u4", "K4", "amazon_au", CANCELLED, "Jason Oakley", ""],
        ["u5", "K5", "amazon_au", "UNREAD", "Jason Oakley", ""],
    ])
    assert track.run(args, session_factory=session_factory, helpers=base) == 0
    got = [(r["order_ref"], r["status"], r["tracking_number"], r["carrier"], r["name_ok"]) for r in results(tmp_path)]
    assert got[:2] == [("u1", "found", "TBAU316654872", "Amazon", "yes"), ("u1", "not_shipped", "", "", "")]
    assert [g[:2] for g in got[2:]] == [("u2", "not_shipped"), ("u3", "error"), ("u4", "cancelled"), ("u5", "error")]


def test_name_differs_from_the_customer(tmp_path, session_factory):
    args = make_run(tmp_path, [["u1", "K1", "amazon_au", TWO_PACKAGES, "Yasmin Harrison", ""]])
    assert track.run(args, session_factory=session_factory, helpers=base) == 0
    assert results(tmp_path)[0]["name_ok"] == "no"


def test_signed_out_stops_the_run(tmp_path, session_factory):
    args = make_run(tmp_path, [
        ["u1", "K1", "amazon_au", SIGNED_OUT, "Elena Legkova", ""],
        ["u2", "K2", "amazon_au", TWO_PACKAGES, "Elena Legkova", ""],
    ])
    assert track.run(args, session_factory=session_factory, helpers=base) == 1
    rows = results(tmp_path)
    assert [r["status"] for r in rows] == ["error", "error"]
    assert "sign in" in rows[1]["error"]


def test_vendor_without_a_tracker_is_refused(tmp_path, session_factory):
    args = make_run(tmp_path, [["u1", "K1", "costco_au", "123", "A B", ""]])
    (tmp_path / "accounts.json").write_text(json.dumps(
        {"au": {"costco_au": [{"name": "Amazon AU", "chrome_profile": "C"}]}}))
    assert track.run(args, session_factory=session_factory, helpers=base) == 2


def test_needs_no_order_bot():
    import sys
    assert "core" not in sys.modules and "vendors" not in sys.modules
