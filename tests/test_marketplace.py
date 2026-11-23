"""Uploading the tracking to Big W and Kogan. The rules (TBA numbers, couriers, which package) are checked as plain
functions; the page steps run in a real Chrome against small local copies of the two portals, built from the
screenshots of the real ones (search, Add Tracking / wrench -> Dispatch / Tracking, save, search again)."""
from types import SimpleNamespace

import openpyxl
import pytest

from tracker.marketplace import (BIGW_ORDERS, KOGAN_MANAGE, MarketplaceSignIn, bigw_shipping, carrier_name,
                                 choose_uploads, courier_for, pick_option)
from tracker.results import ResultFile


# ---- rules ---------------------------------------------------------------------------------------------

def test_carrier_names_and_couriers():
    assert carrier_name("Shipped with Australia Post") == "Australia Post"
    assert carrier_name("AusPost") == "Australia Post"
    assert carrier_name("Delivery By Amazon") == "Amazon"
    assert courier_for("kogan", "Australia Post") == "AUP"
    assert courier_for("bigw", "Shipped with Australia Post") == "Australia Post"
    assert courier_for("bigw", "Amazon") == ""  # never mapped: TBA numbers are not uploaded
    # "Amazon" on the tracking page, but an Australia Post number: picked by the number's format
    assert courier_for("kogan", "Amazon", number="3697B306387401002010905") == "AUP"
    assert courier_for("kogan", "Amazon", number="TBAU317439766") == ""
    assert courier_for("kogan", "", number="ABC123") == ""
    over = {"kogan": {"Australia Post": "Australia Post eParcel"}, "bigw": {"Shipped with Mystery Co": "Other"}}
    assert courier_for("kogan", "Australia Post", over) == "Australia Post eParcel"
    assert courier_for("bigw", "Shipped with Mystery Co", over) == "Other"


def test_pick_option_by_text_or_value():
    opts = [("", "-- Select --"), ("AUP", "Australia Post"), ("FDX", "FedEx"), ("STE", "StarTrack Express")]
    assert pick_option(opts, "AUP") == ("AUP", "Australia Post")
    assert pick_option(opts, "fedex") == ("FDX", "FedEx")
    assert pick_option(opts, "StarTrack") == ("STE", "StarTrack Express")
    assert pick_option(opts, "Select") is None
    assert pick_option(opts, "DHL") is None


def test_choose_uploads_skips_tba_and_other_names():
    rows = [{"status": "found", "tracking_number": "TBAU317388173", "carrier": "Amazon"},
            {"status": "found", "tracking_number": "33TXA5012345", "carrier": "Australia Post", "name_ok": "no"},
            {"status": "found", "tracking_number": "36QT4180587301", "carrier": "Australia Post"},
            {"status": "found", "tracking_number": "36QT999", "carrier": "Australia Post"},
            {"status": "not_shipped", "tracking_number": ""}]
    pick = choose_uploads(rows)
    assert pick is rows[2]
    assert [r.get("upload_status", "") for r in rows] == ["skipped", "skipped", "", "skipped", ""]
    assert "TBA" in rows[0]["upload_note"]
    assert choose_uploads([{"status": "found", "tracking_number": "tbau1234567", "carrier": "Amazon"}]) is None


def test_bigw_shipping_cell():
    text = "DISPATCHED\nDate: 21/11/2026\nCourier: FedEx\nNo: 385177797724\nEdit Remove"
    assert bigw_shipping(text) == ("385177797724", "FedEx")
    assert bigw_shipping("PENDING DISPATCH\nAdd Tracking") == ("", "")
    assert bigw_shipping("BIG W MARKET\n1033075200\nOrder Group No:\n361974787\nPENDING DISPATCH") == ("", "")
    row_text = "1032312660\nOrder Group No: 361953853\nDISPATCHED\nCourier: FedEx\nNo: 385177797724"
    assert bigw_shipping(row_text) == ("385177797724", "FedEx")


# ---- the two portals in a real Chrome ------------------------------------------------------------------

pw = pytest.importorskip("playwright.sync_api")
import tracker.marketplace as marketplace  # noqa: E402
import tracker.page as base  # noqa: E402

import track  # noqa: E402

BIGW_PAGE = """<html><body><h1>Orders</h1>
<p>Search By Order Group No, Order No, Customer Name or Product SKU</p>
<input placeholder="Search" id="q"><button id="go" aria-label="search">&#128269;</button><button>Clear</button>
<table><thead><tr><th>Order No</th><th>Customer Details</th><th>Shipping Status</th><th>Action</th></tr></thead>
<tbody id="rows"></tbody></table>
<div role="dialog" id="dlg" style="display:none"><h3>Add/Edit Tracking</h3>
<label>Tracking Number *</label><input id="tn" placeholder="Enter tracking number">
<label>Courier *</label><select id="cr"><option value="">-- Select --</option><option value="1">Australia Post</option>
<option value="2">FedEx</option><option value="3">StarTrack</option></select>
<button id="cancel">Cancel</button><button id="save">Save</button></div>
<script>
const orders = JSON.parse(localStorage.getItem('bigw') || 'null') ||
  {"1033075200": null, "1032312660": {c: "FedEx", n: "385177797724"}, "1034113440": null};
let cur = null;
function render(f) {
  rows.innerHTML = '';
  for (const [id, t] of Object.entries(orders)) {
    if (f && !id.includes(f)) continue;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>BIG W MARKET<br><a href="#">${id}</a><br>Order Group No: 3619${id.slice(-5)}</td>
      <td>Shannan Braid</td><td>` + (t ? `DISPATCHED<br>Date: 22/11/2026<br>Courier: ${t.c}<br>No: ${t.n}`
      : `PENDING DISPATCH<br><button class="add">Add Tracking</button>`) + `</td><td>View/Add Notes</td>`;
    const b = tr.querySelector('.add');
    if (b) b.onclick = () => { cur = id; tn.value = ''; cr.value = ''; dlg.style.display = 'block'; };
    rows.appendChild(tr);
  }
}
q.addEventListener('keydown', e => { if (e.key === 'Enter') setTimeout(() => render(q.value.trim()), 300); });
go.onclick = () => render(q.value.trim());
cancel.onclick = () => { dlg.style.display = 'none'; };
save.onclick = () => {
  if (!tn.value || !cr.value) return;
  orders[cur] = {c: cr.selectedOptions[0].text, n: tn.value};
  localStorage.setItem('bigw', JSON.stringify(orders));
  setTimeout(() => { dlg.style.display = 'none'; render(q.value.trim()); }, 300);
};
render('');
</script></body></html>"""

KOGAN_PAGE = """<html><body><a href="#">Submit Dispatch Report</a> <a href="#">Manage Orders</a>
<h1>Manage Orders</h1>
Your warehouse code: <input value="AUTFS" readonly>
<button id="openBtn">Open Orders</button><button id="refunded">Refunded Orders</button>
<button id="completed">Completed Orders</button><select><option>Last 7 Days</option></select>
<button id="search">Search Orders</button> <input id="q">
<h2 id="title">Open Orders</h2><table><thead id="head"></thead><tbody id="rows"></tbody></table>
<div id="menu" style="display:none"><b id="mh"></b><button>Cancel Order</button><button>Delay Order</button>
<button id="dispatch">Dispatch / Tracking</button><button>Contact Customer Request</button></div>
<div id="form" style="display:none"><h3 id="fh"></h3>
Carrier: <select id="carrier"><option value="">-- Select --</option><option value="AUP">Australia Post</option>
<option value="FDX">FedEx</option></select>
<div>Tracking Number:</div><input id="tn"><div>Serial Number(s):</div><input id="sn">
<button id="confirmBtn">Confirm Dispatch</button></div>
<script>
const orders = JSON.parse(localStorage.getItem('kogan') || 'null') ||
  {"2QK9BJGC": null, "QTM7YZ3Y": {n: "36QT4180587301000931505", c: "AUP"}, "238YBHAG": null};
let view = 'open', cur = null;
function render(f) {
  const done = view === 'completed' || (f && orders[f]);
  title.textContent = done ? 'Completed Orders' : 'Open Orders';
  head.innerHTML = done ? '<tr><th>Order ID</th><th>Product Code</th><th>Tracking Number</th><th>Carrier Code</th><th></th></tr>'
                        : '<tr><th>Order ID</th><th>Product Code</th><th>Customer Name</th><th></th></tr>';
  rows.innerHTML = '';
  for (const [id, t] of Object.entries(orders)) {
    if (f ? id !== f : (!!t !== (view === 'completed'))) continue;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${id}</td><td>TFS-KAZ-4KV9TB0C72-New</td>` +
      (done ? `<td>${t ? t.n : ''}</td><td>${t ? t.c : ''}</td>` : '<td>Shane vagg</td>') +
      '<td><a href="#" class="tool"><img alt="tools" width="20" height="20" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></a></td>';
    tr.querySelector('.tool').onclick = e => { e.preventDefault(); cur = id; mh.textContent = '1x ' + id; menu.style.display = 'block'; };
    rows.appendChild(tr);
  }
}
search.onclick = () => render(q.value.trim());
openBtn.onclick = () => { view = 'open'; render(''); };
completed.onclick = () => { view = 'completed'; render(''); };
dispatch.onclick = () => { menu.style.display = 'none'; fh.textContent = 'Order: ' + cur; form.style.display = 'block'; };
confirmBtn.onclick = () => {
  if (!carrier.value || !tn.value) { alert('Select a carrier'); return; }
  if (!window.confirm('Dispatch this order?')) return;
  orders[cur] = {n: tn.value, c: carrier.value};
  localStorage.setItem('kogan', JSON.stringify(orders));
  form.style.display = 'none'; render(q.value.trim());
};
render('');
</script></body></html>"""

SIGN_IN_PAGE = "<html><body><h1>Login</h1><input name='u'><input type='password' name='p'></body></html>"


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
        page.wait_for_timeout(150)
    except Exception:
        pass


@pytest.fixture
def portal_session(browser, monkeypatch):
    """A session factory whose Chrome answers the portals with the local copies (``pages`` can swap one)."""
    monkeypatch.setattr(base, "settle", fast_settle)
    monkeypatch.setattr(base, "PAUSE_MIN_MS", 1)
    monkeypatch.setattr(base, "PAUSE_MAX_MS", 2)
    monkeypatch.setattr(marketplace, "SEARCH_WAIT_S", 3)
    pages = {BIGW_ORDERS: BIGW_PAGE, KOGAN_MANAGE: KOGAN_PAGE}
    ctx = browser.new_context()  # one context for the test, so what was saved is there on the next run

    def route(url):
        return lambda r: r.fulfill(status=200, content_type="text/html", body=pages[url])

    for url in pages:
        ctx.route(url, route(url))

    class Session:
        opened = []

        def __init__(self, account):
            Session.opened.append(account)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def new_page(self):
            pg = ctx.new_page()
            pg.set_default_timeout(5000)
            return pg

    Session.pages = pages
    yield Session
    ctx.close()


def upload_run(tmp_path, marketplace, orders, rows, session, cfg=None):
    results = ResultFile(tmp_path / "out" / "tracking-done.xlsx")
    results.rows = rows
    args = SimpleNamespace(marketplace=marketplace, store="Kogan-TFS" if marketplace == "kogan" else "BigW store",
                           output_dir=str(tmp_path / "out"))
    stop = track.upload_tracking(args, cfg or {}, orders, results, base, session)
    results.save()
    saved = list(openpyxl.load_workbook(results.path).active.iter_rows(values_only=True))
    return stop, [dict(zip(saved[0], r)) for r in saved[1:]]


def row(ref, number, carrier="Australia Post", **kw):
    return {"order_ref": ref, "invoice": "", "vendor_order_number": "503-1", "status": "found",
            "tracking_number": number, "carrier": carrier, "ship_name": "", "name_ok": "yes", "error": "", **kw}


def test_bigw_uploads_checks_and_leaves_the_rest(tmp_path, portal_session):
    orders = [{"order_ref": "u1", "invoice": "1033075200"}, {"order_ref": "u2", "invoice": "1032312660"},
              {"order_ref": "u3", "invoice": "1034113440"}, {"order_ref": "u4", "invoice": "999"}]
    rows = [row("u1", "33TXA5012345"), row("u2", "36QT11111"), row("u3", "TBAU317388173", "Amazon"),
            row("u4", "36QT22222")]
    stop, got = upload_run(tmp_path, "bigw", orders, rows, portal_session)
    assert stop == ""
    assert [r["upload_status"] for r in got] == ["verified", "skipped", "skipped", "failed"]
    assert "Courier Australia Post".lower() in got[0]["upload_note"].lower()
    assert "385177797724" in got[1]["upload_note"]  # another number already there: not overwritten
    assert "TBA" in got[2]["upload_note"]
    assert "not found" in got[3]["upload_note"] and (tmp_path / "out" / "upload-bigw-999.png").exists()
    assert portal_session.opened[0].name == "BigW store"

    # the next run finds it already there
    stop, got = upload_run(tmp_path, "bigw", orders[:1], [row("u1", "33TXA5012345")], portal_session)
    assert got[0]["upload_status"] == "already"


def test_kogan_dispatches_and_checks(tmp_path, portal_session):
    orders = [{"order_ref": "u1", "invoice": "2QK9BJGC"}, {"order_ref": "u2", "invoice": "QTM7YZ3Y"}]
    rows = [row("u1", "36QT4180587301000931999"), row("u2", "36QT4180587301000931505")]
    stop, got = upload_run(tmp_path, "kogan", orders, rows, portal_session)
    assert stop == ""
    assert [r["upload_status"] for r in got] == ["verified", "already"]
    assert "AUP" in got[0]["upload_note"]


def test_dry_run_saves_nothing(tmp_path, portal_session):
    orders = [{"order_ref": "u1", "invoice": "238YBHAG"}]
    stop, got = upload_run(tmp_path, "kogan", orders, [row("u1", "36QT5555")], portal_session,
                           {"upload_dry_run": True})
    assert got[0]["upload_status"] == "dry_run"
    stop, got = upload_run(tmp_path, "kogan", orders, [row("u1", "36QT5555")], portal_session)
    assert got[0]["upload_status"] == "verified"  # still open after the dry run, so it is dispatched now


def test_unknown_courier_and_upload_off(tmp_path, portal_session):
    orders = [{"order_ref": "u1", "invoice": "2QK9BJGC"}]
    stop, got = upload_run(tmp_path, "kogan", orders, [row("u1", "XYZ123456", "Mystery Couriers")], portal_session)
    assert got[0]["upload_status"] == "failed" and "couriers" in got[0]["upload_note"]
    stop, got = upload_run(tmp_path, "kogan", orders, [row("u1", "36QT1")], portal_session,
                           {"upload_tracking": False})
    assert got[0]["upload_status"] == "" or got[0]["upload_status"] is None


def test_signed_out_portal_stops_the_uploads(tmp_path, portal_session):
    portal_session.pages[KOGAN_MANAGE] = SIGN_IN_PAGE
    orders = [{"order_ref": "u1", "invoice": "2QK9BJGC"}, {"order_ref": "u2", "invoice": "238YBHAG"}]
    stop, got = upload_run(tmp_path, "kogan", orders, [row("u1", "36QT1"), row("u2", "36QT2")], portal_session)
    assert "sign in" in stop
    assert [r["upload_status"] for r in got] == ["failed", "failed"]
    assert MarketplaceSignIn


def test_upload_only_job_from_the_orders_page(tmp_path, portal_session):
    """The Upload button: the orders file carries the tracking number; Amazon is never opened."""
    wb = openpyxl.Workbook()
    wb.active.append(["order_ref", "invoice", "vendor", "vendor_order_number", "customer_name", "purchased_at",
                      "tracking_number", "carrier"])
    wb.active.append(["u1", "2QK9BJGC", "amazon_au", "503-1", "Shane vagg", "", "3697B306387401002010905", "Amazon"])
    wb.active.append(["u2", "238YBHAG", "amazon_au", "503-2", "Sue", "", "TBAU317439766", "Amazon"])
    wb.save(tmp_path / "orders.xlsx")
    args = SimpleNamespace(orders=str(tmp_path / "orders.xlsx"), accounts_file=str(tmp_path / "missing.json"),
                           account="", output_dir=str(tmp_path / "out"), config="", marketplace="kogan",
                           store="Kogan-TFS", upload_only=True)

    def no_amazon(account):
        raise AssertionError("Amazon must not be opened")

    assert track.run(args, session_factory=no_amazon, helpers=base, upload_session_factory=portal_session) == 0
    saved = list(openpyxl.load_workbook(tmp_path / "out" / "tracking-done.xlsx").active.iter_rows(values_only=True))
    got = [dict(zip(saved[0], r)) for r in saved[1:]]
    assert [(r["order_ref"], r["upload_status"]) for r in got] == [("u1", "verified"), ("u2", "skipped")]
    assert "AUP" in got[0]["upload_note"]


def test_agent_passes_upload_only():
    import tracking_agent
    job = {"job_id": "j", "kind": "upload_tracking", "account": "", "marketplace": "bigw", "store_name": "P"}
    cmd = tracking_agent.build_command("python", tmp := __import__("pathlib").Path("w"), job)
    assert "--upload-only" in cmd and cmd[cmd.index("--marketplace") + 1] == "bigw"
    assert "--upload-only" not in tracking_agent.build_command("python", tmp, {**job, "kind": "tracking"})


def test_upload_uses_the_courier_the_web_app_sent(tmp_path, portal_session):
    """Upload tracking on the Orders page sends the courier the user saved; the bot picks exactly that one,
    also for a carrier it does not know itself."""
    wb = openpyxl.Workbook()
    wb.active.append(["order_ref", "invoice", "vendor", "vendor_order_number", "customer_name", "purchased_at",
                      "tracking_number", "carrier", "tracking_id", "courier"])
    wb.active.append(["u1", "2QK9BJGC", "amazon_au", "503-1", "Shane vagg", "", "XYZ123456789", "Mystery Couriers",
                      "t1", "AUP"])
    wb.save(tmp_path / "orders.xlsx")
    args = SimpleNamespace(orders=str(tmp_path / "orders.xlsx"), accounts_file=str(tmp_path / "missing.json"),
                           account="", output_dir=str(tmp_path / "out"), config="", marketplace="kogan",
                           store="Kogan-TFS", upload_only=True)
    assert track.run(args, session_factory=None, helpers=base, upload_session_factory=portal_session) == 0
    saved = list(openpyxl.load_workbook(tmp_path / "out" / "tracking-done.xlsx").active.iter_rows(values_only=True))
    got = dict(zip(saved[0], saved[1]))
    assert got["upload_status"] == "verified" and "AUP" in got["upload_note"]
