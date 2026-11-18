from datetime import date

from pydantic import BaseModel, Field, field_validator


class DeliveryLine(BaseModel):
    product_code: str | None = Field(None, description="Supplier or internal product reference")
    description: str
    quantity: float = Field(gt=0)
    uom: str | None = None
    lot: str | None = Field(None, description="Lot / batch number")
    expiration_date: date | None = None
    unit_price: float | None = Field(None, ge=0)


class DeliveryNote(BaseModel):
    supplier_name: str
    supplier_vat: str | None = None
    document_number: str
    document_date: date | None = None
    purchase_order: str | None = Field(None, description="Customer PO reference, e.g. P00102")
    lines: list[DeliveryLine] = Field(min_length=1)

    @field_validator("supplier_vat")
    @classmethod
    def clean_vat(cls, v: str | None) -> str | None:
        return v.replace(" ", "").replace("-", "").upper() if v else v
