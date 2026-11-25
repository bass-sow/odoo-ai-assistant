"""Read-only tools the agent can call. Each one is a typed function over Odoo or the docs.

The agent never writes to Odoo: anything that would change data is returned as a
proposal for a person to validate.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.odoo.client import OdooClient


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., object]

    def schema(self) -> dict:
        params = self.args.model_json_schema()
        params.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }


class SearchProductsArgs(BaseModel):
    query: str = Field(description="Product name or internal reference, e.g. 'yogurt' or 'MOZ-125'")


class ExpiringLotsArgs(BaseModel):
    days: int = Field(30, ge=0, le=365, description="Window in days from today")
    product: str | None = Field(None, description="Optional product name or reference filter")


class StockByLocationArgs(BaseModel):
    product: str = Field(description="Product name or internal reference")


class PendingDeliveriesArgs(BaseModel):
    customer: str | None = Field(None, description="Optional customer name filter")
    days_ahead: int = Field(7, ge=0, le=60, description="Deliveries scheduled up to N days ahead")


class OverdueInvoicesArgs(BaseModel):
    customer: str | None = Field(None, description="Optional customer name filter")
    min_days: int = Field(1, ge=1, le=365, description="Only invoices at least N days past due")


class SearchDocsArgs(BaseModel):
    question: str = Field(description="What to look up in the warehouse procedures")


def _m2o(value) -> str | None:
    return value[1] if value else None


def build_tools(odoo: OdooClient, retriever=None) -> dict[str, Tool]:
    def search_products(query: str):
        rows = odoo.search_read(
            "product.product", [("name", "ilike", query)],
            ["name", "default_code", "qty_available", "uom_id"], limit=10)
        if not rows:
            rows = odoo.search_read(
                "product.product", [("default_code", "ilike", query)],
                ["name", "default_code", "qty_available", "uom_id"], limit=10)
        return [{"reference": r["default_code"], "name": r["name"],
                 "qty_available": r["qty_available"], "uom": _m2o(r["uom_id"])} for r in rows]

    def expiring_lots(days: int = 30, product: str | None = None):
        limit_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        domain = [("expiration_date", "!=", False), ("expiration_date", "<=", limit_date)]
        if product:
            domain.append(("product_id", "ilike", product))
        rows = odoo.search_read("stock.lot", domain,
                                ["name", "product_id", "expiration_date", "product_qty"],
                                limit=100, order="expiration_date asc")
        now = datetime.now()
        out = []
        for r in rows:
            if r["product_qty"] <= 0:
                continue
            exp = datetime.strptime(r["expiration_date"], "%Y-%m-%d %H:%M:%S")
            days_left = (exp.date() - now.date()).days
            out.append({"lot": r["name"], "product": _m2o(r["product_id"]),
                        "expiration_date": r["expiration_date"][:10], "days_left": days_left,
                        "status": "expired" if days_left < 0 else "expiring",
                        "quantity": r["product_qty"]})
        return out

    def stock_by_location(product: str):
        rows = odoo.search_read(
            "stock.quant",
            [("product_id", "ilike", product), ("location_id.usage", "=", "internal"),
             ("quantity", ">", 0)],
            ["product_id", "location_id", "lot_id", "quantity", "reserved_quantity"], limit=100)
        return [{"product": _m2o(r["product_id"]), "location": _m2o(r["location_id"]),
                 "lot": _m2o(r["lot_id"]), "quantity": r["quantity"],
                 "reserved": r["reserved_quantity"]} for r in rows]

    def pending_deliveries(customer: str | None = None, days_ahead: int = 7):
        until = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d 23:59:59")
        domain = [("picking_type_code", "=", "outgoing"),
                  ("state", "in", ["waiting", "confirmed", "assigned"]),
                  ("scheduled_date", "<=", until)]
        if customer:
            domain.append(("partner_id", "ilike", customer))
        rows = odoo.search_read("stock.picking", domain,
                                ["name", "partner_id", "scheduled_date", "state", "origin"],
                                limit=50, order="scheduled_date asc")
        return [{"reference": r["name"], "customer": _m2o(r["partner_id"]),
                 "scheduled": r["scheduled_date"][:16], "state": r["state"],
                 "sale_order": r["origin"]} for r in rows]

    def overdue_invoices(customer: str | None = None, min_days: int = 1):
        today = datetime.now().date()
        cutoff = (today - timedelta(days=min_days - 1)).isoformat()
        domain = [("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                  ("payment_state", "in", ["not_paid", "partial"]),
                  ("invoice_date_due", "<", cutoff)]
        if customer:
            domain.append(("partner_id", "ilike", customer))
        rows = odoo.search_read("account.move", domain,
                                ["name", "partner_id", "invoice_date_due", "amount_total",
                                 "amount_residual", "payment_state", "currency_id"],
                                limit=100, order="invoice_date_due asc")
        invoices, totals = [], {}
        for r in rows:
            due = datetime.strptime(r["invoice_date_due"], "%Y-%m-%d").date()
            name = _m2o(r["partner_id"])
            invoices.append({"invoice": r["name"], "customer": name,
                             "due_date": r["invoice_date_due"], "days_overdue": (today - due).days,
                             "amount_due": r["amount_residual"], "currency": _m2o(r["currency_id"]),
                             "partially_paid": r["payment_state"] == "partial"})
            totals[name] = round(totals.get(name, 0) + r["amount_residual"], 2)
        return {"invoices": invoices, "total_due_by_customer": totals}

    def search_docs(question: str):
        if retriever is None:
            return {"error": "documentation index not available"}
        hits = retriever.retrieve(question, k=3)
        return [{"source": h.chunk.source, "title": h.chunk.title, "text": h.chunk.text}
                for h in hits] or {"result": "nothing relevant found in the procedures"}

    tools = [
        Tool("search_products", "Find products by name or internal reference, with stock "
             "on hand.", SearchProductsArgs, search_products),
        Tool("get_expiring_lots", "List lots in stock that are already expired or expire "
             "within the given number of days, soonest first.", ExpiringLotsArgs, expiring_lots),
        Tool("get_stock_by_location", "Where a product is stored: quantity per internal "
             "location and lot.", StockByLocationArgs, stock_by_location),
        Tool("get_pending_deliveries", "Outgoing deliveries not done yet, scheduled up to N "
             "days ahead.", PendingDeliveriesArgs, pending_deliveries),
        Tool("get_overdue_invoices", "Customer invoices past their due date and not fully "
             "paid, oldest first, with the amount still due per customer.",
             OverdueInvoicesArgs, overdue_invoices),
        Tool("search_procedures", "Search the warehouse procedures (receiving, FEFO picking, "
             "expired stock, returns, barcode scanning).", SearchDocsArgs, search_docs),
    ]
    return {t.name: t for t in tools}
