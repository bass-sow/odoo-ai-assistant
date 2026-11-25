"""Demo dataset: a small food distributor (dairy, sauces, frozen goods).

Expiration dates are relative to today so the demo never goes stale.
"""

from datetime import datetime, timedelta

from app.odoo.client import MockOdoo


def _date(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def build_mock() -> MockOdoo:
    locations = [
        {"id": 8, "complete_name": "WH/Stock", "usage": "internal"},
        {"id": 21, "complete_name": "WH/Stock/Cold Room", "usage": "internal"},
        {"id": 22, "complete_name": "WH/Stock/Freezer", "usage": "internal"},
        {"id": 23, "complete_name": "WH/Stock/Dry Store A", "usage": "internal"},
        {"id": 30, "complete_name": "WH/Quarantine", "usage": "internal"},
        {"id": 5, "complete_name": "Partners/Customers", "usage": "customer"},
    ]
    loc = {r["id"]: [r["id"], r["complete_name"]] for r in locations}

    products = [
        (1, "Greek Yogurt 500g", "YOG-500", 21, "Units"),
        (2, "Fresh Mozzarella 125g", "MOZ-125", 21, "Units"),
        (3, "Whole Milk 1L", "MLK-1000", 21, "Units"),
        (4, "Tomato Sauce 400g", "SAU-400", 23, "Units"),
        (5, "Frozen Spinach 1kg", "SPI-1000", 22, "Units"),
        (6, "Olive Oil 5L", "OIL-5000", 23, "Units"),
    ]
    product_rows = [
        {"id": pid, "name": name, "default_code": code, "uom_id": [1, uom], "tracking": "lot",
         "qty_available": 0.0}
        for pid, name, code, _, uom in products
    ]
    prod = {p["id"]: [p["id"], f"[{p['default_code']}] {p['name']}"] for p in product_rows}

    # (lot id, lot name, product, expires in N days, quantity, location)
    lot_specs = [
        (101, "YOG-2611-A", 1, -2, 36, 21),
        (102, "YOG-2611-B", 1, 4, 120, 21),
        (103, "YOG-2612-A", 1, 19, 240, 21),
        (104, "MOZ-2611-C", 2, 6, 80, 21),
        (105, "MOZ-2612-A", 2, 25, 150, 21),
        (106, "MLK-2611-F", 3, 9, 300, 21),
        (107, "SAU-2603-B", 4, 140, 500, 23),
        (108, "SPI-2605-A", 5, 210, 90, 22),
        (109, "OIL-2702-A", 6, 420, 60, 23),
        (110, "MOZ-2610-Z", 2, -9, 12, 30),
    ]
    lots, quants = [], []
    for lot_id, name, pid, days, qty, loc_id in lot_specs:
        lots.append({"id": lot_id, "name": name, "product_id": prod[pid],
                     "expiration_date": _date(days), "product_qty": float(qty)})
        quants.append({"id": 1000 + lot_id, "product_id": prod[pid], "lot_id": [lot_id, name],
                       "location_id": loc[loc_id], "quantity": float(qty),
                       "reserved_quantity": 0.0})
    for p in product_rows:
        p["qty_available"] = sum(
            q["quantity"] for q in quants if q["product_id"][0] == p["id"] and q["location_id"][0] != 30
        )

    partners = [
        {"id": 41, "name": "Lácteos del Vallès SL", "vat": "ESB12345678", "supplier_rank": 1,
         "customer_rank": 0},
        {"id": 42, "name": "Conservas Ebro SA", "vat": "ESA87654321", "supplier_rank": 1,
         "customer_rank": 0},
        {"id": 51, "name": "Restaurante La Plaça", "vat": "ESB11223344", "supplier_rank": 0,
         "customer_rank": 1},
        {"id": 52, "name": "Hotel Mar Blau", "vat": "ESB55667788", "supplier_rank": 0,
         "customer_rank": 1},
        {"id": 53, "name": "Supermercat Terrassa Centre", "vat": "ESB99887766",
         "supplier_rank": 0, "customer_rank": 1},
    ]
    part = {p["id"]: [p["id"], p["name"]] for p in partners}

    pickings = [
        {"id": 301, "name": "WH/OUT/00412", "partner_id": part[51], "scheduled_date": _date(0),
         "state": "assigned", "picking_type_code": "outgoing", "origin": "S00388"},
        {"id": 302, "name": "WH/OUT/00413", "partner_id": part[52], "scheduled_date": _date(1),
         "state": "confirmed", "picking_type_code": "outgoing", "origin": "S00391"},
        {"id": 303, "name": "WH/OUT/00414", "partner_id": part[53], "scheduled_date": _date(1),
         "state": "assigned", "picking_type_code": "outgoing", "origin": "S00392"},
        {"id": 304, "name": "WH/OUT/00409", "partner_id": part[51], "scheduled_date": _date(-3),
         "state": "done", "picking_type_code": "outgoing", "origin": "S00380"},
        {"id": 305, "name": "WH/IN/00157", "partner_id": part[41], "scheduled_date": _date(2),
         "state": "assigned", "picking_type_code": "incoming", "origin": "P00102"},
    ]

    # Customer invoices; "days" is the due date relative to today.
    invoice_specs = [
        (401, "INV/2026/00231", 51, -41, 1840.50, 1840.50, "not_paid"),
        (402, "INV/2026/00244", 51, -12, 620.00, 620.00, "not_paid"),
        (403, "INV/2026/00250", 52, -19, 2310.00, 1155.00, "partial"),
        (404, "INV/2026/00262", 53, -3, 980.40, 980.40, "not_paid"),
        (405, "INV/2026/00270", 52, 12, 1430.00, 1430.00, "not_paid"),
        (406, "INV/2026/00215", 53, -30, 760.00, 0.0, "paid"),
    ]
    invoices = [
        {"id": inv_id, "name": name, "partner_id": part[pid], "move_type": "out_invoice",
         "state": "posted", "invoice_date_due": _date(days)[:10], "amount_total": total,
         "amount_residual": residual, "payment_state": pay_state,
         "currency_id": [1, "EUR"]}
        for inv_id, name, pid, days, total, residual, pay_state in invoice_specs
    ]

    data = {
        "stock.location": locations,
        "product.product": product_rows,
        "stock.lot": lots,
        "stock.quant": quants,
        "res.partner": partners,
        "stock.picking": pickings,
        "account.move": invoices,
    }
    relations = {
        "product.product": {},
        "stock.lot": {"product_id": "product.product"},
        "stock.quant": {"product_id": "product.product", "lot_id": "stock.lot",
                        "location_id": "stock.location"},
        "stock.picking": {"partner_id": "res.partner"},
        "account.move": {"partner_id": "res.partner"},
    }
    return MockOdoo(data, relations)
