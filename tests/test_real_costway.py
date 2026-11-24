"""track.py for a Costway account in a real Chrome: Costway's shipments page is answered with local copies built
from the live page (au.costway.com/shipments?orderNumber=AU000469713: "Shipment #1", status, carrier, number)."""
import json
from types import SimpleNamespace

import openpyxl
import pytest

pw = pytest.importorskip("playwright.sync_api")

import tracker.page as base  # noqa: E402
from tests.test_real_amazon import browser, fast_settle  # noqa: E402,F401  (the module's Chrome)

import track  # noqa: E402

CW = "https://au.costway.com"
HEADER = ("<div class='banner'>Black Friday Preview is LIVE! Grow your savings up to 13% OFF <a>Claim Now</a></div>"
          "<nav>Furniture Outdoor Kitchen</nav><a>Back</a>")


def shipments(*shipments, status="In transit"):
    tabs = "".join(f"<button class='tab' onclick='show({i})'>Shipment #{i + 1}</button>" for i in range(len(shipments)))
    boxes = "".join(
        f"<div class='box' id='s{i}' style='display:{'block' if i == 0 else 'none'}'>"
        f"<div class='head'>Your order is {status}</div><div>{c}</div><div class='num'>{n}</div></div>"
        for i, (c, n) in enumerate(shipments))
    return (f"<html><body>{HEADER}{tabs}{boxes}<script>function show(i){{document.querySelectorAll('.box')"
            f".forEach((b, k) => b.style.display = k === i ? 'block' : 'none');}}</script></body></html>")


PAGES = {
    "orderNumber=AU000469713": shipments(("AustraliaPost", "34NDE615400901000931508")),
    "orderNumber=AU000500001": shipments(("AustraliaPost", "34NDE615400901000931508"),
                                         ("Couriers Please", "CPAPS1234567890")),
    "orderNumber=AU000500002": f"<html><body>{HEADER}<p>Your order is Processing</p></body></html>",
    "orderNumber=AU000500003": "<html><body><script>location.replace('/customer/account/login/')</script></body></html>",
    "/customer/account/login": "<html><body><h1>Customer Login</h1><input name='login[username]'></body></html>",
}


@pytest.fixture
def session_factory(browser, monkeypatch):  # noqa: F811
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
            self.ctx.route(f"{CW}/**", handler)
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
        {"au": {"costway_au": [{"name": "Costway AU", "chrome_profile": "Profile 1"}]}}))
    return SimpleNamespace(orders=str(tmp_path / "orders.xlsx"), accounts_file=str(tmp_path / "accounts.json"),
                           account="Costway AU", output_dir=str(tmp_path / "out"), config="")


def results(tmp_path):
    rows = list(openpyxl.load_workbook(tmp_path / "out" / "tracking-done.xlsx").active.iter_rows(values_only=True))
    return [{k: ("" if v is None else v) for k, v in zip(rows[0], r)} for r in rows[1:]]


def test_costway_shipments(tmp_path, session_factory):
    args = make_run(tmp_path, [
        ["u1", "K1", "costway_au", "AU000469713", "Braddon Tan", ""],
        ["u2", "K2", "costway_au", "AU000500001", "Braddon Tan", ""],
        ["u3", "K3", "costway_au", "AU000500002", "Braddon Tan", ""],
        ["u4", "K4", "costway_au", "503-1234567-1234567", "Braddon Tan", ""],
    ])
    assert track.run(args, session_factory=session_factory, helpers=base) == 0
    got = [(r["order_ref"], r["status"], r["tracking_number"], r["carrier"]) for r in results(tmp_path)]
    assert got == [
        ("u1", "found", "34NDE615400901000931508", "AustraliaPost"),
        ("u2", "found", "34NDE615400901000931508", "AustraliaPost"),
        ("u2", "found", "CPAPS1234567890", "Couriers Please"),
        ("u3", "not_shipped", "", ""),
        ("u4", "error", "", ""),
    ]


def test_costway_signed_out_stops_the_run(tmp_path, session_factory):
    args = make_run(tmp_path, [["u1", "K1", "costway_au", "AU000500003", "Braddon Tan", ""],
                               ["u2", "K2", "costway_au", "AU000469713", "Braddon Tan", ""]])
    assert track.run(args, session_factory=session_factory, helpers=base) == 1
    rows = results(tmp_path)
    assert [r["status"] for r in rows] == ["error", "error"] and "sign in to Costway" in rows[1]["error"]
