"""Input (orders.xlsx from the web app) and output (tracking-done.xlsx back to it)."""
import logging
import os
import time
from pathlib import Path

import openpyxl

log = logging.getLogger("trackingbot")

ORDER_COLUMNS = ["order_ref", "invoice", "vendor", "vendor_order_number", "customer_name", "purchased_at"]
RESULT_COLUMNS = ["order_ref", "invoice", "vendor_order_number", "status", "tracking_number", "carrier",
                  "ship_name", "name_ok", "error"]
SAVE_TRIES, SAVE_WAIT_S = 40, 0.5


class OrdersFileError(Exception):
    pass


def read_orders(path):
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise OrdersFileError(f"orders file could not be opened: {type(e).__name__}: {e}") from None
    try:
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        raise OrdersFileError("orders file is empty")
    header = [str(c or "").strip() for c in rows[0]]
    missing = [c for c in ("order_ref", "vendor_order_number") if c not in header]
    if missing:
        raise OrdersFileError(f"orders file has no column {', '.join(missing)}")
    out = []
    for row in rows[1:]:
        d = {h: ("" if v is None else str(v).strip()) for h, v in zip(header, row)}
        if d.get("order_ref"):
            out.append(d)
    return out


def split_numbers(raw):
    return [n.strip() for n in str(raw or "").replace(";", ",").split(",") if n.strip()]


class ResultFile:
    """Rows written after every order, so the web app gets what was read even if the run stops."""

    def __init__(self, path, sleep=time.sleep):
        self.path = Path(path)
        self.rows = []
        self.sleep = sleep

    def add(self, order, number, pkg):
        self.rows.append({"order_ref": order.get("order_ref", ""), "invoice": order.get("invoice", ""),
                          "vendor_order_number": number, **{k: pkg.get(k, "") for k in RESULT_COLUMNS[3:]}})

    def save(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(RESULT_COLUMNS)
        for r in self.rows:
            ws.append([str(r.get(c, "") or "") for c in RESULT_COLUMNS])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp.xlsx")
        wb.save(tmp)
        for i in range(SAVE_TRIES):  # the agent may be reading the file for a moment (Windows refuses replace)
            try:
                os.replace(tmp, self.path)
                return True
            except PermissionError:
                self.sleep(SAVE_WAIT_S)
        log.warning("%s NOT SAVED (file busy); it is saved again after the next order", self.path.name)
        return False
