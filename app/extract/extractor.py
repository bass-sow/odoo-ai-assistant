"""Delivery note (albarán) -> validated JSON -> matched against Odoo.

The output is a proposal: matched supplier, matched products and the lines that need a
person to look at them. Nothing is written to Odoo.
"""

import json
import re

from pydantic import ValidationError

from app.extract.schemas import DeliveryNote
from app.odoo.client import OdooClient

PROMPT = """Extract the data of this supplier delivery note as JSON matching this schema:

{schema}

Rules:
- Dates as YYYY-MM-DD. Numbers without thousands separators, dot as decimal mark.
- Use null for anything not present. Do not guess lot numbers or expiration dates.
- GS1 barcodes: (01)=GTIN, (10)=lot, (17)=expiration YYMMDD, (37)=quantity.
- Return only the JSON object."""


class ExtractionError(ValueError):
    pass


def extract_delivery_note(text: str, llm, max_attempts: int = 2) -> DeliveryNote:
    messages = [
        {"role": "system", "content": PROMPT.format(
            schema=json.dumps(DeliveryNote.model_json_schema()))},
        {"role": "user", "content": text},
    ]
    last_error = ""
    for _ in range(max_attempts):
        result = llm.chat(messages, json_mode=True, operation="extract")
        raw = (result.content or "").strip().removeprefix("```json").removesuffix("```")
        try:
            return DeliveryNote.model_validate_json(raw)
        except ValidationError as e:
            last_error = str(e)
            # Give the model its own output and the validation errors, ask for a fix.
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"That JSON is invalid:\n{last_error}\n"
                                            "Return the corrected JSON only."},
            ]
    raise ExtractionError(f"Could not extract a valid delivery note: {last_error}")


def match_with_odoo(note: DeliveryNote, odoo: OdooClient, country_prefix: str = "ES") -> dict:
    supplier = None
    if note.supplier_vat:
        # Odoo stores EU VAT numbers with the country prefix (ESB12345678); delivery notes
        # usually print the bare NIF/CIF (B-12345678).
        vats = [note.supplier_vat]
        if not re.match(r"^[A-Z]{2}", note.supplier_vat):
            vats.append(f"{country_prefix}{note.supplier_vat}")
        rows = odoo.search_read("res.partner", [("vat", "in", vats)], ["name"], limit=1)
        supplier = rows[0] if rows else None
    if supplier is None:
        rows = odoo.search_read("res.partner", [("name", "ilike", note.supplier_name)],
                                ["name"], limit=1)
        supplier = rows[0] if rows else None

    lines, issues = [], []
    for i, line in enumerate(note.lines, 1):
        product = None
        if line.product_code:
            rows = odoo.search_read("product.product", [("default_code", "=", line.product_code)],
                                    ["name", "default_code", "tracking"], limit=1)
            product = rows[0] if rows else None
        entry = {**line.model_dump(mode="json"),
                 "odoo_product": product and {"id": product["id"], "name": product["name"]}}
        if not product:
            issues.append(f"Line {i}: product '{line.product_code or line.description}' "
                          "not found in Odoo")
        elif product.get("tracking") == "lot" and not line.lot:
            issues.append(f"Line {i}: {product['name']} is lot-tracked but no lot was read")
        elif product.get("tracking") == "lot" and not line.expiration_date:
            issues.append(f"Line {i}: lot {line.lot} has no expiration date")
        lines.append(entry)

    if not supplier:
        issues.insert(0, f"Supplier '{note.supplier_name}' not found in Odoo")
    return {
        "document": note.model_dump(mode="json", exclude={"lines"}),
        "supplier": supplier and {"id": supplier["id"], "name": supplier["name"]},
        "lines": lines,
        "issues": issues,
        "ready_to_import": not issues,
    }


def pdf_to_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
