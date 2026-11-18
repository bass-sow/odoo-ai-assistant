"""Access to Odoo data.

OdooXmlRpc talks to a real instance (any version with external API: 15-19).
MockOdoo serves an in-memory dataset with the same models and field names, so the
assistant can be developed and demoed without an Odoo server.
"""

import operator
import xmlrpc.client
from typing import Protocol


class OdooClient(Protocol):
    def search_read(
        self, model: str, domain: list, fields: list[str], limit: int = 80, order: str = ""
    ) -> list[dict]: ...


class OdooXmlRpc:
    def __init__(self, url: str, db: str, user: str, api_key: str):
        self.url, self.db, self.api_key = url.rstrip("/"), db, api_key
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.uid = common.authenticate(db, user, api_key, {})
        if not self.uid:
            raise PermissionError("Odoo authentication failed: check ODOO_USER / ODOO_API_KEY")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    def search_read(self, model, domain, fields, limit=80, order=""):
        kwargs = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return self.models.execute_kw(
            self.db, self.uid, self.api_key, model, "search_read", [domain], kwargs
        )


_OPS = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
    "ilike": lambda a, b: str(b).lower() in str(a or "").lower(),
    "like": lambda a, b: str(b) in str(a or ""),
}


class MockOdoo:
    """Supports implicit-AND domains, the usual operators and dotted paths on many2one."""

    def __init__(self, data: dict[str, list[dict]], relations: dict[str, dict[str, str]]):
        self.data = data
        self.relations = relations

    def _value(self, model: str, record: dict, path: str, op: str):
        field, _, rest = path.partition(".")
        value = record.get(field)
        comodel = self.relations.get(model, {}).get(field)
        if comodel and isinstance(value, list | tuple):
            if rest:
                related = next((r for r in self.data[comodel] if r["id"] == value[0]), {})
                return self._value(comodel, related, rest, op)
            # Many2one: compare names for text operators, ids otherwise (like Odoo).
            return value[1] if op in ("ilike", "like") else value[0]
        if comodel and not value:
            return False
        return value

    def _match(self, model: str, record: dict, domain: list) -> bool:
        for leaf in domain:
            if isinstance(leaf, str):  # '&' is implicit; '|' and '!' are not needed here
                continue
            path, op, expected = leaf
            value = self._value(model, record, path, op)
            if value is False and op in ("<", "<=", ">", ">="):
                return False
            if not _OPS[op](value, expected):
                return False
        return True

    def search_read(self, model, domain, fields, limit=80, order=""):
        rows = [r for r in self.data.get(model, []) if self._match(model, r, domain)]
        if order:
            key, _, direction = order.partition(" ")
            rows.sort(key=lambda r: (r.get(key) is False, r.get(key) or ""),
                      reverse=direction.strip().lower() == "desc")
        if not fields and rows:
            fields = [k for k in rows[0] if k != "id"]
        return [{"id": r["id"], **{f: r.get(f, False) for f in fields}} for r in rows[:limit]]


def build_odoo(settings) -> OdooClient:
    if settings.odoo_mode == "live":
        return OdooXmlRpc(settings.odoo_url, settings.odoo_db, settings.odoo_user,
                          settings.odoo_api_key)
    from app.odoo.mock_data import build_mock

    return build_mock()
