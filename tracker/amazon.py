"""Amazon: open an order in the purchase account, click Track package, read the tracking ID and carrier.

Each "Track package" button is one package. The tracking page shows, for a shipped package:

    Delivery By Amazon                 Shipping Address
    Tracking ID: TBAU316654872         Elena Legkova
                                       U 22 21 Lacey Rd ...

A package that has not left yet has no Tracking ID on that page (and an order that has not shipped at all may
have no Track package button).
"""
import logging
import re

log = logging.getLogger("trackingbot")

ORDER_DETAILS = "/gp/your-account/order-details?orderID={number}"
ORDER_NUMBER_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")
TRACK_BUTTONS = ["a:has-text('Track package')", "span.a-button:has-text('Track package')",
                 "button:has-text('Track package')", "input[value='Track package']"]
SIGN_IN_URL_RE = re.compile(r"/ap/(signin|mfa|cvf)", re.I)
# The ID may be on the next line; it always has a digit (so a following word like "Shipping" is not taken).
TRACKING_ID_RE = re.compile(r"Tracking\s*(?:ID|number)\s*[:#]?\s*((?=[A-Z\-]*\d)[A-Z0-9][A-Z0-9\-]{4,40})", re.I)
CARRIER_RES = (
    re.compile(r"Delivery\s+by\s+([^\n|]{2,60})", re.I),
    re.compile(r"Ship(?:ped|s)\s+with\s+([^\n|]{2,60})", re.I),
    re.compile(r"Carrier\s*[:\-]\s*([^\n|]{2,60})", re.I),
)
NOT_FOUND_RE = re.compile(r"(unable to (?:load|find) (?:your|this) order|order (?:was )?not found|"
                          r"we can.t find (?:that|this) order)", re.I)
CANCELLED_RE = re.compile(r"\b(?:this order (?:has been|was) cancell?ed|cancell?ed)\b", re.I)


class SignInNeeded(Exception):
    """Amazon shows its sign-in page: the account's Chrome profile is signed out."""


class Package(dict):
    """One result row: status found / not_shipped / cancelled / error, tracking_number, carrier, ship_name."""


def clean_tracking(raw):
    return re.sub(r"\s+", "", raw or "").strip("-").upper()


def parse_tracking_page(text):
    """(tracking number, carrier, ship name) read from a Track package page's text ('' when not shown)."""
    text = text or ""
    m = TRACKING_ID_RE.search(text)
    number = clean_tracking(m.group(1).split("\n")[0]) if m else ""
    carrier = ""
    for rx in CARRIER_RES:
        c = rx.search(text)
        if c:
            carrier = " ".join(c.group(1).split()).strip(" .:")
            carrier = re.split(r"\s{2,}|\bTracking\b", carrier, flags=re.I)[0].strip(" .:")
            break
    name = ""
    s = re.search(r"Shipping\s+address\s*\n+\s*([^\n]+)", text, re.I)
    if s:
        name = " ".join(s.group(1).split())
    return number, carrier, name


def name_tokens(name):
    return {t for t in re.findall(r"[a-z]+", (name or "").lower()) if len(t) > 1}


def names_match(a, b):
    """True / False, or None when one of the names is unknown. Same words in any order (middle names allowed)."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return None
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= big


class AmazonTracker:
    def __init__(self, base, helpers):
        """``helpers``: the order bot's vendors.base module (goto, settle, body_text, first_visible, click_loc)."""
        self.base = base.rstrip("/")
        self.h = helpers

    def guard(self, page):
        if SIGN_IN_URL_RE.search(page.url or ""):
            raise SignInNeeded("Amazon asks to sign in: open the purchase account's Chrome profile on this laptop "
                               "and sign in to Amazon, then run Get tracking again")

    def open_order(self, page, number):
        self.h.goto(page, self.base + ORDER_DETAILS.format(number=number))
        self.h.settle(page, 1500)
        self.guard(page)

    def track_buttons(self, page):
        """The visible Track package buttons, top to bottom (one per package)."""
        for sel in TRACK_BUTTONS:
            loc = page.locator(f"{sel} >> visible=true")
            try:
                n = loc.count()
            except Exception:
                n = 0
            if n:
                return loc, n
        return None, 0

    def read_order(self, page, number, customer=""):
        """Every package of one Amazon order: a list of Package rows."""
        if not ORDER_NUMBER_RE.match(number or ""):
            return [Package(status="error", error=f"'{number}' is not an Amazon order number")]
        log.info("    order %s: open order details", number)
        self.open_order(page, number)
        text = self.h.body_text(page)
        buttons, count = self.track_buttons(page)
        log.info("    order %s: %d Track package button(s)", number, count)
        if not count:
            if NOT_FOUND_RE.search(text):
                return [Package(status="error", error=f"order {number} is not in this Amazon account")]
            if CANCELLED_RE.search(text):
                return [Package(status="cancelled", error=f"order {number} is cancelled on Amazon")]
            return [Package(status="not_shipped", error=f"order {number}: no Track package button yet")]
        rows, seen = [], set()
        for i in range(count):
            if i:
                self.open_order(page, number)  # back to the order for the next package
                buttons, n = self.track_buttons(page)
                if i >= n:
                    break
            log.info("    order %s: click Track package %d of %d", number, i + 1, count)
            before = page.url
            self.h.click_loc(page, buttons.nth(i))
            self.h.settle(page, 1500)
            self.guard(page)
            if page.url == before:  # some layouts open the tracking in a popover on the same page
                page.wait_for_timeout(1500)
            tracking, carrier, ship_name = parse_tracking_page(self.h.body_text(page))
            ok = names_match(ship_name, customer)
            if tracking:
                if tracking in seen:
                    continue
                seen.add(tracking)
                log.info("    order %s: package %d tracking %s, carrier %s, ship to %s%s", number, i + 1, tracking,
                         carrier or "?", ship_name or "?", "" if ok is not False else "  (NOT the customer's name)")
                rows.append(Package(status="found", tracking_number=tracking, carrier=carrier, ship_name=ship_name,
                                    name_ok="" if ok is None else "yes" if ok else "no"))
            else:
                log.info("    order %s: package %d has no tracking ID yet", number, i + 1)
                rows.append(Package(status="not_shipped", ship_name=ship_name,
                                    error=f"order {number}: package {i + 1} has no tracking ID yet"))
        return rows or [Package(status="not_shipped", error=f"order {number}: no tracking ID yet")]
