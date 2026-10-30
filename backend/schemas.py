import datetime
import re
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