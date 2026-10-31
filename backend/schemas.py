import datetime
import re
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _to_number(v: Any) -> float | None:
    """Accept 1234.5, "1,234.50" or "KES 1,234.50". Anything unreadable becomes None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[^\d.\-]", "", v.replace(",", ""))
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _to_text(v: Any) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v.strip() or None
    return None


def _to_date(v: Any) -> datetime.date | None:
    """Accept an ISO date (YYYY-MM-DD). Anything else becomes None."""
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def _to_currency(v: Any) -> str | None:
    text = _to_text(v)
    if text and re.fullmatch(r"[A-Za-z]{3}", text):
        return text.upper()
    return None


def _to_items(v: Any) -> list:
    if not isinstance(v, list):
        return []
    return [item for item in v if isinstance(item, (dict, LineItem))]


def _to_totals(v: Any) -> Any:
    return v if isinstance(v, (dict, Totals)) else {}


Number = Annotated[float | None, BeforeValidator(_to_number)]
Text = Annotated[str | None, BeforeValidator(_to_text)]


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: Text = None
    quantity: Number = None
    unit_price: Number = None
    amount: Number = None


class Totals(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subtotal: Number = None
    tax: Number = None
    total: Number = None


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supplier: Text = None
    invoice_number: Text = None
    date: Annotated[datetime.date | None, BeforeValidator(_to_date)] = None
    currency: Annotated[str | None, BeforeValidator(_to_currency)] = None
    line_items: Annotated[list[LineItem], BeforeValidator(_to_items)] = Field(default_factory=list)
    totals: Annotated[Totals, BeforeValidator(_to_totals)] = Field(default_factory=Totals)

class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"

class FlagCode(StrEnum):
    MISSING_FIELD = "missing_field"
    NO_LINE_ITEMS = "no_line_items"
    LINE_AMOUNT_MISMATCH = "line_amount_mismatch"
    SUBTOTAL_MISMATCH = "subtotal_mismatch"
    TOTAL_MISMATCH = "total_mismatch"
    NEGATIVE_VALUE = "negative_value"
    DATE_IN_FUTURE = "date_in_future"
    DATE_TOO_OLD = "date_too_old"
    UNKNOWN_CURRENCY = "unknown_currency"
    SUPPLIER_UNKNOWN = "supplier_unknown"
    SUPPLIER_NEAR_MATCH = "supplier_near_match"
    DUPLICATE_FILE = "duplicate_file"


class Flag(BaseModel):
    """One problem found on an invoice, tied to the field it refers to.

    `field` is a path such as "supplier", "totals.total" or "line_items[2].amount".
    """

    code: FlagCode
    severity: Severity
    field: str
    message: str