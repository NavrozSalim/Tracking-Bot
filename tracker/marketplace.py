"""Upload the tracking read from the vendor to the marketplace order, then search the order again to check it.

The marketplace order ID is the orders file's ``invoice`` column (Kogan OrderID such as 2QK9BJGC, Big W Order No
such as 1033075200), never the vendor's (Amazon) order number.

Big W (Woolworths MarketPlus portal, also MyDeal / Everyday Market orders):
    /orders -> Search box: order no -> Search -> Add Tracking -> Tracking Number + Courier -> Save
    check: search again, the Shipping Status cell shows "Courier: ... No: <tracking>".
Kogan (dispatch.aws.kgn.io/manage):
    Search Orders box: order ID -> Search Orders -> wrench icon -> Dispatch / Tracking -> Carrier + Tracking
    Number -> Confirm Dispatch
    check: search again, the row shows the Tracking Number and Carrier Code.

Not uploaded: Amazon's own "TBA..." tracking numbers, an order whose ship-to name is not the customer, and an
order that already shows another tracking number on the marketplace (it is never overwritten).
"""
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("trackingbot")

BIGW_ORDERS = "https://portal.woolworthsmarketplus.com.au/orders"
KOGAN_MANAGE = "https://dispatch.aws.kgn.io/manage"
SEARCH_WAIT_S = 20
SAVE_WAIT_S = 15

# Amazon's own delivery numbers: never uploaded to a marketplace.
NO_UPLOAD_RE = re.compile(r"^TBA", re.I)

# The carrier text read on the vendor's tracking page -> one carrier name. First match wins.
CARRIER_ALIASES = (
    ("Australia Post", ("australia post", "auspost", "aus post", "australiapost")),
    ("StarTrack", ("startrack", "star track")),
    ("CouriersPlease", ("couriers please", "couriersplease")),
    ("Aramex", ("aramex", "fastway")),
    ("DHL", ("dhl",)),
    ("Team Global Express", ("team global express", "tge", "toll")),
    ("TNT", ("tnt",)),
    ("FedEx", ("fedex", "fed ex")),
    ("Sendle", ("sendle",)),
    ("Allied Express", ("allied",)),
    ("Hunter Express", ("hunter express",)),
    ("Direct Freight Express", ("direct freight",)),
    ("Amazon", ("amazon",)),
)

# Carrier name -> what to pick in the marketplace's Courier / Carrier list (matched with the option's text or
# value, ignoring case). tracking.json "couriers" adds to / replaces these:
#     "couriers": {"kogan": {"Australia Post": "AUP"}, "bigw": {"Australia Post": "Australia Post"}}
DEFAULT_COURIERS = {
    "bigw": {"Australia Post": "Australia Post", "StarTrack": "StarTrack", "CouriersPlease": "Couriers Please",
             "Aramex": "Aramex", "DHL": "DHL", "Team Global Express": "Team Global Express", "TNT": "TNT",
             "FedEx": "FedEx", "Sendle": "Sendle", "Allied Express": "Allied Express",
             "Hunter Express": "Hunter Express", "Direct Freight Express": "Direct Freight Express"},
    "kogan": {"Australia Post": "AUP", "StarTrack": "StarTrack", "CouriersPlease": "Couriers Please",
              "Aramex": "Aramex", "DHL": "DHL", "Team Global Express": "Team Global Express", "TNT": "TNT",
              "FedEx": "FedEx", "Sendle": "Sendle", "Allied Express": "Allied Express",
              "Hunter Express": "Hunter Express", "Direct Freight Express": "Direct Freight Express"},
}
MARKETPLACE_LABEL = {"bigw": "Big W", "kogan": "Kogan"}


class MarketplaceSignIn(Exception):
    """The marketplace shows its sign-in page: the store's Chrome profile is signed out."""


class UploadError(Exception):
    pass


def norm(text):
    return " ".join(str(text or "").split()).lower()


def clean_number(text):
    return re.sub(r"\s+", "", str(text or "")).upper()


def carrier_name(raw):
    """One carrier name for the vendor's carrier text ('' when unknown)."""
    t = norm(raw)
    if not t:
        return ""
    for name, words in CARRIER_ALIASES:
        if any(w in t for w in words):
            return name
    return ""


def courier_for(marketplace, raw_carrier, overrides=None):
    """What to pick in the marketplace's courier list for the vendor's carrier ('' when there is no mapping).
    tracking.json may map the vendor's text itself ("Shipped with Australia Post") or the carrier name."""
    table = {norm(k): v for k, v in DEFAULT_COURIERS.get(marketplace, {}).items()}
    table.update({norm(k): v for k, v in ((overrides or {}).get(marketplace) or {}).items()})
    for key in (norm(raw_carrier), norm(carrier_name(raw_carrier))):
        if key and table.get(key):
            return table[key]
    return ""


def pick_option(options, target):
    """The (value, label) option for ``target``: same text or value first, then a label that contains it."""
    t = norm(target)
    if not t:
        return None
    usable = [(v, lb) for v, lb in options if norm(lb) and not norm(lb).startswith("--") and norm(lb) != "select"]
    for v, lb in usable:
        if norm(lb) == t or norm(v) == t:
            return v, lb
    for v, lb in usable:
        if t in norm(lb) or (len(norm(lb)) > 2 and norm(lb) in t):
            return v, lb
    return None


def choose_uploads(rows):
    """Which result row of one order is uploaded. Sets upload_status / upload_note on the rows that are not.
    Returns the row to upload, or None."""
    found = [r for r in rows if r.get("status") == "found" and clean_number(r.get("tracking_number"))]
    pick = None
    for r in found:
        number = clean_number(r.get("tracking_number"))
        if NO_UPLOAD_RE.match(number):
            r["upload_status"], r["upload_note"] = "skipped", "Amazon TBA number: not uploaded to the marketplace"
        elif str(r.get("name_ok") or "").lower() == "no":
            r["upload_status"], r["upload_note"] = "skipped", ("ship-to name is not the customer: check it, then "
                                                               "upload by hand")
        elif pick is None:
            pick = r
        else:
            r["upload_status"], r["upload_note"] = "skipped", (f"the marketplace takes one tracking number; "
                                                               f"{pick.get('tracking_number')} was uploaded")
    return pick


def order_id_re(order_id):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(order_id) + r"(?![A-Za-z0-9])", re.I)


# ---- page bits shared by both portals ------------------------------------------------------------------

def visible(loc):
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def first_visible(*locs):
    for loc in locs:
        try:
            n = loc.count()
        except Exception:
            continue
        for i in range(min(n, 20)):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                pass
    return None


def select_courier(page, scope, target):
    """Pick ``target`` in the Courier / Carrier list (a <select>, or a drop-down that lists options when clicked).
    Returns the label picked; raises UploadError listing the options when it is not there."""
    sel = first_visible(scope.locator("select"))
    if sel is not None:
        options = sel.evaluate("s => [...s.options].map(o => [o.value, (o.textContent || '').trim()])")
        hit = pick_option([tuple(o) for o in options], target)
        if hit is None:
            raise UploadError(f"courier '{target}' is not in the list ({', '.join(lb for _, lb in options if lb)})")
        sel.select_option(value=hit[0])
        return hit[1]
    box = first_visible(scope.locator("[role=combobox]"), scope.get_by_text(re.compile(r"--\s*Select\s*--", re.I)))
    if box is None:
        raise UploadError("the courier list was not found in the tracking form")
    box.click()
    page.wait_for_timeout(600)
    items = page.locator("[role=option]:visible, [role=listbox] li:visible, ul[class*=menu i] li:visible")
    labels = []
    for i in range(min(items.count(), 200)):
        try:
            labels.append(" ".join(items.nth(i).inner_text(timeout=1000).split()))
        except Exception:
            labels.append("")
    hit = pick_option([(lb, lb) for lb in labels], target)
    if hit is None:
        page.keyboard.press("Escape")
        raise UploadError(f"courier '{target}' is not in the list ({', '.join(x for x in labels if x)})")
    items.nth(labels.index(hit[1])).click()
    return hit[1]


class Portal:
    key = ""
    url = ""

    def __init__(self, page, helpers, dry_run=False, shots=None):
        self.page, self.h, self.dry_run = page, helpers, dry_run
        self.shots = Path(shots) if shots else None

    @property
    def label(self):
        return MARKETPLACE_LABEL.get(self.key, self.key)

    def guard(self):
        url = (self.page.url or "").lower()
        if re.search(r"/(login|signin|sign-in|auth|oauth|sso)\b", url) or visible(
                self.page.locator("input[type=password]")):
            raise MarketplaceSignIn(f"{self.label} asks to sign in: run open_profile.bat with the store's name "
                                    f"and {self.key}, sign in to the seller portal, then run Get tracking again")

    def open(self):
        self.h.goto(self.page, self.url)
        self.h.settle(self.page, 1500)
        self.guard()

    def screenshot(self, order_id):
        if not self.shots:
            return ""
        try:
            self.shots.mkdir(parents=True, exist_ok=True)
            path = self.shots / f"upload-{self.key}-{re.sub(r'[^A-Za-z0-9]+', '_', order_id)}.png"
            self.page.screenshot(path=str(path), full_page=True)
            return path.name
        except Exception:
            return ""

    def wait_row(self, rows, order_id, seconds=None):
        """The visible row of ``order_id`` once the search shows it (None when it never does). The row is found
        by its text, not its place, so a list that is drawn again after the search still gives the right row."""
        match = rows.filter(has_text=order_id)
        rx = order_id_re(order_id)  # the whole ID: 1033075200 is not part of 21033075200
        end = time.monotonic() + (SEARCH_WAIT_S if seconds is None else seconds)
        while True:
            try:
                n = match.count()
            except Exception:
                n = 0
            for i in range(min(n, 50)):
                row = match.nth(i)
                try:
                    if row.is_visible() and rx.search(row.inner_text(timeout=2000)):
                        return row
                except Exception:
                    pass
            if time.monotonic() >= end:
                return None
            self.page.wait_for_timeout(700)

    def upload(self, order_id, tracking, courier):
        """(status, note): verified / already / not_verified / dry_run / skipped. Raises on a failure."""
        raise NotImplementedError


# ---- Big W ---------------------------------------------------------------------------------------------

def bigw_shipping(text):
    """(tracking number, courier) shown in a Big W order row ('' when none)."""
    text = text or ""
    courier = re.search(r"Courier\s*:\s*([^\n]+)", text)
    # The row also says "Order Group No: 361974787": only a "No:" in the shipping status counts.
    tail = text[courier.start():] if courier else text
    number = re.search(r"(?<!Group )(?<!Order )\bNo\s*[:.]\s*([A-Za-z0-9\-]{5,})", tail)
    return (clean_number(number.group(1)) if number else "",
            " ".join(courier.group(1).split()) if courier else "")


class BigW(Portal):
    key = "bigw"
    url = BIGW_ORDERS

    def search(self, order_id):
        self.open()
        box = first_visible(self.page.get_by_placeholder(re.compile(r"^\s*search", re.I)),
                            self.page.locator("input[type=search]"))
        if box is None:
            raise UploadError("Big W: the order search box was not found on the Orders page")
        log.info("      search %s", order_id)
        box.click()
        box.fill("")
        box.fill(order_id)
        self.h.pause(self.page, 200, 500)
        box.press("Enter")
        self.h.settle(self.page, 1500)  # the list shown before the search is drawn again
        row = self.wait_row(self.page.locator("tbody tr"), order_id, min(8, SEARCH_WAIT_S))
        if row is None:  # Enter did not search: press the magnifier button next to the box
            btn = first_visible(self.page.locator("button[aria-label*=search i]"),
                                box.locator("xpath=following::button[1]"))
            if btn is not None:
                self.h.click_loc(self.page, btn)
                row = self.wait_row(self.page.locator("tbody tr"), order_id)
        return row

    def form(self):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            dlg = first_visible(self.page.locator("[role=dialog]").filter(has_text=re.compile("Tracking", re.I)),
                                self.page.locator(".modal").filter(has_text=re.compile("Tracking", re.I)))
            if dlg is not None:
                return dlg
            self.page.wait_for_timeout(400)
        raise UploadError("Big W: the Add/Edit Tracking form did not open")

    def upload(self, order_id, tracking, courier):
        row = self.search(order_id)
        if row is None:
            raise UploadError(f"Big W: order {order_id} was not found by the search")
        shown, shown_courier = bigw_shipping(row.inner_text())
        if shown == clean_number(tracking):
            return "already", f"Big W already shows {shown} ({shown_courier or 'courier ?'})"
        if shown:
            return "skipped", f"Big W already shows another tracking number {shown}: not overwritten"
        btn = first_visible(row.get_by_role("button", name=re.compile(r"Add Tracking", re.I)),
                            row.locator("button:has-text('Add Tracking'), a:has-text('Add Tracking')"))
        if btn is None:
            raise UploadError("Big W: no Add Tracking button on the order (" +
                              " ".join(row.inner_text().split())[:120] + ")")
        self.h.click_loc(self.page, btn)
        dlg = self.form()
        box = first_visible(dlg.get_by_placeholder(re.compile("tracking number", re.I)),
                            dlg.locator("input[type=text], input:not([type])"))
        if box is None:
            raise UploadError("Big W: the Tracking Number box was not found in the form")
        box.click()
        box.fill(tracking)
        picked = select_courier(self.page, dlg, courier)
        log.info("      Big W form: tracking %s, courier %s", tracking, picked)
        self.h.pause(self.page)
        if self.dry_run:
            cancel = first_visible(dlg.get_by_role("button", name=re.compile(r"^\s*Cancel\s*$", re.I)))
            if cancel is not None:
                cancel.click()
            else:
                self.page.keyboard.press("Escape")
            return "dry_run", f"dry run: form filled with {tracking} / {picked}, not saved"
        save = first_visible(dlg.get_by_role("button", name=re.compile(r"^\s*Save\s*$", re.I)),
                             dlg.locator("button:has-text('Save')"))
        if save is None:
            raise UploadError("Big W: no Save button in the tracking form")
        self.h.click_loc(self.page, save)
        end = time.monotonic() + SAVE_WAIT_S
        while time.monotonic() < end and visible(dlg):
            self.page.wait_for_timeout(500)
        if visible(dlg):
            raise UploadError("Big W: the tracking form did not close after Save (" +
                              " ".join(dlg.inner_text().split())[:200] + ")")
        self.h.settle(self.page, 1500)
        return self.verify(order_id, tracking, picked)

    def verify(self, order_id, tracking, picked):
        row = self.search(order_id)
        shown, shown_courier = bigw_shipping(row.inner_text()) if row is not None else ("", "")
        if shown == clean_number(tracking):
            note = f"Big W shows {shown}, courier {shown_courier or '?'}"
            if shown_courier and pick_option([(shown_courier, shown_courier)], picked) is None:
                note += f" (picked {picked})"
            return "verified", note
        return "not_verified", (f"saved, but the search shows {shown or 'no tracking'} on order {order_id}: "
                                f"check it on Big W")


# ---- Kogan ---------------------------------------------------------------------------------------------

class Kogan(Portal):
    key = "kogan"
    url = KOGAN_MANAGE

    def rows(self):
        return self.page.locator("tbody tr")

    def search(self, order_id, completed=False):
        self.open()
        if completed:
            btn = first_visible(self.page.locator("button:has-text('Completed Orders'), "
                                                  "input[value='Completed Orders']"))
            if btn is not None:
                self.h.click_loc(self.page, btn)
                self.h.settle(self.page, 1500)
        btn = first_visible(self.page.locator("button:has-text('Search Orders'), input[value='Search Orders']"))
        if btn is None:
            raise UploadError("Kogan: the Search Orders button was not found on the Manage Orders page")
        box = first_visible(btn.locator("xpath=following::input[not(@type='hidden')][1]"),
                            btn.locator("xpath=preceding::input[not(@type='hidden')][1]"))
        if box is None:
            raise UploadError("Kogan: the order search box was not found")
        log.info("      search %s", order_id)
        box.click()
        box.fill("")
        box.fill(order_id)
        self.h.pause(self.page, 200, 500)
        self.h.click_loc(self.page, btn)
        self.h.settle(self.page, 1000)
        return self.wait_row(self.rows(), order_id)

    def shown_tracking(self, row):
        """(tracking number, carrier code) in the row when the grid has those columns ('' otherwise)."""
        try:
            heads = [norm(t) for t in self.page.locator("th:visible").all_inner_texts()]
            cells = [" ".join(t.split()) for t in row.locator("td").all_inner_texts()]
        except Exception:
            return "", ""
        if "tracking number" not in heads:
            return "", ""
        # Grids may draw an extra cell at the start (row expander); line the cells up from the right.
        off = len(cells) - len(heads)
        get = lambda name: cells[heads.index(name) + off] if name in heads and 0 <= heads.index(name) + off < len(
            cells) else ""
        return clean_number(get("tracking number")), get("carrier code") or get("carrier")

    def upload(self, order_id, tracking, courier):
        row = self.search(order_id)
        if row is None:
            row = self.search(order_id, completed=True)
            if row is None:
                raise UploadError(f"Kogan: order {order_id} was not found by Search Orders")
        shown, code = self.shown_tracking(row)
        if not shown and clean_number(tracking) in clean_number(row.inner_text()):
            shown = clean_number(tracking)
        if shown == clean_number(tracking):
            return "already", f"Kogan already shows {shown} ({code or 'carrier ?'})"
        if shown:
            return "skipped", f"Kogan already shows another tracking number {shown}: not overwritten"
        tools = row.locator("td").last
        wrench = first_visible(tools.locator("a, button, img, i, span"), tools)
        self.h.click_loc(self.page, wrench)
        self.page.wait_for_timeout(800)
        item = first_visible(
            self.page.locator("button, a, input[type=button], input[type=submit]").filter(
                has_text=re.compile(r"Dispatch\s*/\s*Tracking", re.I)),
            self.page.locator("input[value*='Dispatch / Tracking']"),
            self.page.get_by_text(re.compile(r"^\s*Dispatch\s*/\s*Tracking\s*$", re.I)))
        if item is None:
            raise UploadError("Kogan: Dispatch / Tracking is not in the order's menu (the order may be cancelled "
                              "or already dispatched)")
        self.h.click_loc(self.page, item)
        self.page.wait_for_timeout(800)
        confirm = first_visible(
            self.page.locator("button, input[type=button], input[type=submit], a").filter(
                has_text=re.compile(r"Confirm\s+Dispatch", re.I)),
            self.page.locator("input[value*='Confirm Dispatch']"))
        if confirm is None:
            raise UploadError("Kogan: the dispatch form (Confirm Dispatch) did not open")
        form = confirm.locator("xpath=ancestor::*[.//select][1]")
        if not visible(form):
            form = self.page
        box = first_visible(form.locator("xpath=.//*[contains(normalize-space(text()), 'Tracking Number')]"
                                         "/following::input[not(@type='hidden')][1]"),
                            form.locator("input[type=text], input:not([type])"))
        if box is None:
            raise UploadError("Kogan: the Tracking Number box was not found in the dispatch form")
        picked = select_courier(self.page, form, courier)
        box.click()
        box.fill(tracking)
        log.info("      Kogan form: carrier %s, tracking %s", picked, tracking)
        self.h.pause(self.page)
        if self.dry_run:
            self.page.keyboard.press("Escape")
            return "dry_run", f"dry run: form filled with {picked} / {tracking}, Confirm Dispatch not pressed"
        # A browser "are you sure?" box after Confirm Dispatch is answered OK.
        self.page.once("dialog", lambda d: d.accept())
        self.h.click_loc(self.page, confirm)
        self.h.settle(self.page, 2500)
        return self.verify(order_id, tracking, picked)

    def verify(self, order_id, tracking, picked):
        want = clean_number(tracking)
        for completed in (False, True):
            row = self.search(order_id, completed=completed)
            if row is None:
                continue
            shown, code = self.shown_tracking(row)
            if shown == want or (not shown and want in clean_number(row.inner_text())):
                return "verified", f"Kogan shows {want}, carrier {code or picked}"
        return "not_verified", f"Confirm Dispatch pressed, but the search does not show {want}: check it on Kogan"


PORTALS = {"bigw": BigW, "kogan": Kogan}
