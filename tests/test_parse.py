from tracker.amazon import names_match, parse_tracking_page
from tracker.results import ResultFile, read_orders, split_numbers

SHOT = """Ordered
Shipped
Out for delivery
Delivered
Delivery By Amazon
Tracking ID: TBAU316654872
See all updates
Update delivery instructions
Shipping Address
Elena Legkova
U 22 21 Lacey Rd
CARSELDINE, QLD 4034
Order Info
View order details"""


def test_amazon_tracking_page_from_the_screenshot():
    assert parse_tracking_page(SHOT) == ("TBAU316654872", "Amazon", "Elena Legkova")


def test_other_carrier_and_id_on_the_next_line():
    text = "Shipped with Australia Post\nTracking ID\n33TXA5012345 01000931509\nShipping address\nJason Oakley\n"
    number, carrier, name = parse_tracking_page(text)
    assert (number, carrier, name) == ("33TXA5012345", "Australia Post", "Jason Oakley")


def test_not_shipped_page_has_no_number():
    text = "Ordered\nPreparing for dispatch\nTracking ID\nShipping Address\nElena Legkova"
    assert parse_tracking_page(text)[0] == ""


def test_names():
    assert names_match("Elena Legkova", "elena  LEGKOVA") is True
    assert names_match("Elena Maria Legkova", "Elena Legkova") is True
    assert names_match("Elena Legkova", "Yasmin Harrison") is False
    assert names_match("", "Yasmin Harrison") is None


def test_orders_file_and_results(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["order_ref", "invoice", "vendor", "vendor_order_number", "customer_name", "purchased_at"])
    wb.active.append(["u1", "K1", "amazon_au", "503-1111111-1111111, 503-2222222-2222222", "Elena Legkova", ""])
    wb.active.append([None, "", "", "", "", ""])
    wb.save(tmp_path / "orders.xlsx")
    orders = read_orders(tmp_path / "orders.xlsx")
    assert len(orders) == 1
    assert split_numbers(orders[0]["vendor_order_number"]) == ["503-1111111-1111111", "503-2222222-2222222"]

    rf = ResultFile(tmp_path / "out" / "tracking-done.xlsx")
    rf.add(orders[0], "503-1111111-1111111", {"status": "found", "tracking_number": "TB1", "carrier": "Amazon"})
    assert rf.save()
    rows = list(openpyxl.load_workbook(tmp_path / "out" / "tracking-done.xlsx").active.iter_rows(values_only=True))
    got = dict(zip(rows[0], rows[1]))
    assert (got["order_ref"], got["status"], got["tracking_number"]) == ("u1", "found", "TB1")


def test_busy_result_file_is_retried(tmp_path, monkeypatch):
    import os
    calls = []
    real = os.replace

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("busy")
        return real(a, b)

    monkeypatch.setattr("tracker.results.os.replace", flaky)
    rf = ResultFile(tmp_path / "tracking-done.xlsx", sleep=lambda s: None)
    assert rf.save() and len(calls) == 3
