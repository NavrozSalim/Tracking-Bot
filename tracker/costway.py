"""Costway AU: open the order's shipments page and read each shipment's carrier and tracking number.

    https://au.costway.com/shipments?orderNumber=AU000469713

    Shipment #1   Shipment #2            (one tab per package)
    Your order is In transit
    AustraliaPost
    34NDE615400901000931508

The page is opened in the Costway purchase account's Chrome profile (Costway may ask to sign in). An order with no
shipment yet shows no carrier / number: "not shipped".
"""
import logging
import re

from .amazon import Package, SignInNeeded, clean_tracking

log = logging.getLogger("trackingbot")

SHIPMENTS = "/shipments?orderNumber={number}"
ORDER_NUMBER_RE = re.compile(r"^[A-Z]{0,4}\d{6,}$", re.I)
SIGN_IN_URL_RE = re.compile(r"/customer/account/(login|create)", re.I)
SHIPMENT_TABS = "text=/^\\s*Shipment\\s*#\\s*\\d+\\s*$/"
# A carrier line, then the tracking number on its own line (letters and digits, with at least one digit).
SHIPMENT_RE = re.compile(r"(?m)^[ \t]*([A-Za-z][A-Za-z .&'()-]{1,40}?)[ \t]*\n+[ \t]*"
                         r"((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9 -]{6,40}[A-Z0-9])[ \t]*$", re.I)
NOT_FOUND_RE = re.compile(r"(no (?:order|shipment)s? (?:was |were )?found|order (?:was )?not found|"
                          r"we can.t find (?:that|this|your) order)", re.I)
CANCELLED_RE = re.compile(r"\b(?:order (?:has been|was|is) cancell?ed|cancell?ed)\b", re.I)
# Lines that are part of the page, never a carrier.
NOT_CARRIER = re.compile(r"^(your order is|shipment|back|order|status|track|search|black friday|claim now)", re.I)


def parse_shipment(text):
    """(carrier, tracking number) of the shipment shown ('' when the page shows none)."""
    for m in SHIPMENT_RE.finditer(text or ""):
        carrier = " ".join(m.group(1).split())
        if NOT_CARRIER.match(carrier):
            continue
        return carrier, clean_tracking(m.group(2))
    return "", ""


class CostwayTracker:
    def __init__(self, base, helpers):
        """``helpers``: tracker.page (goto, settle, body_text, click_loc)."""
        self.base = base.rstrip("/")
        self.h = helpers

    def guard(self, page):
        if SIGN_IN_URL_RE.search(page.url or ""):
            raise SignInNeeded("Costway asks to sign in: open the Costway purchase account's Chrome profile on this PC "
                               "and sign in to Costway, then run Get tracking again")

    def open_order(self, page, number):
        self.h.goto(page, self.base + SHIPMENTS.format(number=number))
        self.h.settle(page, 2000)
        self.guard(page)

    def tabs(self, page):
        try:
            loc = page.locator(f"{SHIPMENT_TABS} >> visible=true")
            return loc, loc.count()
        except Exception:
            return None, 0

    def read_order(self, page, number, customer=""):
        """Every shipment of one Costway order: a list of Package rows."""
        if not ORDER_NUMBER_RE.match(number or ""):
            return [Package(status="error", error=f"'{number}' is not a Costway order number")]
        log.info("    order %s: open Costway shipments", number)
        self.open_order(page, number)
        tabs, count = self.tabs(page)
        log.info("    order %s: %d shipment tab(s)", number, count)
        rows, seen = [], set()
        for i in range(max(1, count)):
            if count > 1 and i:
                try:
                    self.h.click_loc(page, tabs.nth(i))
                    self.h.settle(page, 800)
                except Exception as e:
                    log.info("    order %s: shipment %d tab not opened (%s)", number, i + 1, type(e).__name__)
                    continue
            text = self.h.body_text(page)
            carrier, tracking = parse_shipment(text)
            if tracking and tracking not in seen:
                seen.add(tracking)
                log.info("    order %s: shipment %d tracking %s, carrier %s", number, i + 1, tracking, carrier or "?")
                rows.append(Package(status="found", tracking_number=tracking, carrier=carrier, ship_name="", name_ok=""))
            elif not tracking and count:
                rows.append(Package(status="not_shipped", error=f"order {number}: shipment {i + 1} has no tracking yet"))
        if rows:
            return rows
        text = self.h.body_text(page)
        if NOT_FOUND_RE.search(text):
            return [Package(status="error", error=f"order {number} is not in this Costway account")]
        if CANCELLED_RE.search(text):
            return [Package(status="cancelled", error=f"order {number} is cancelled on Costway")]
        return [Package(status="not_shipped", error=f"order {number}: no shipment on Costway yet")]
