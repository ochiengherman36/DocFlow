import math
from decimal import ROUND_HALF_UP, Decimal

from schemas import InvoiceData, LineItem

CENT = Decimal("0.01")
MAX_BILL_NUMBER_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2000
MAX_NOTES_LENGTH = 5000
TAX_DESCRIPTION = "Tax as shown on the invoice"
ADJUSTMENT_DESCRIPTION = "Rounding difference on the invoice"


class BillError(ValueError):
    """The invoice cannot be turned into a bill. The message says what to fix."""


def missing_currency_message(code: str) -> str:
    return f"Zoho Books does not have the currency {code}. Add it under Settings, Currencies, then try again."


def _number(value: float | None) -> Decimal | None:
    if value is None or not math.isfinite(value):
        return None
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _line(index: int, item: LineItem, account_id: str) -> tuple[dict, Decimal]:
    number = index + 1
    quantity, price, amount = _number(item.quantity), _number(item.unit_price), _number(item.amount)

    if amount is None:
        if price is None:
            raise BillError(f"Line {number} has no amount or unit price.")
        amount = price * (quantity if quantity is not None else Decimal(1))
    amount = _money(amount)
    if amount < 0:
        raise BillError(f"Line {number} is negative. Credit notes cannot be sent as bills.")

    description = item.description or f"Line {number}"
    if quantity is not None and price is not None and quantity > 0 and quantity * price == amount:
        line_quantity, rate = quantity, price
    else:
        line_quantity, rate = Decimal(1), amount
        if quantity is not None and price is not None:
            description = f"{description} (qty {float(quantity):g} x {price:,.2f})"

    line = {
        "account_id": account_id,
        "description": description[:MAX_DESCRIPTION_LENGTH],
        "quantity": float(line_quantity),
        "rate": float(rate),
    }
    return line, amount


def payload_total(payload: dict) -> Decimal:
    """What Zoho Books will make of the bill: every line at quantity times rate, plus the adjustment."""
    total = sum(
        (_money(Decimal(str(line["quantity"])) * Decimal(str(line["rate"]))) for line in payload["line_items"]),
        Decimal(0),
    )
    return total + Decimal(str(payload.get("adjustment", 0)))


def build_bill(
    invoice: InvoiceData,
    *,
    vendor_id: str,
    account_id: str,
    currency_id: str | None = None,
    notes: str | None = None,
    tolerance: float = 0.01,
) -> dict:
    """The body for a Zoho Books bill, made from an invoice. Nothing is sent and the invoice is not changed.

    The bill total always equals the invoice total. The stated tax becomes its own line, and any small
    rounding difference the checks allowed becomes the bill's adjustment. If the lines and the total
    differ by more than that, a BillError says so instead of sending a wrong bill.
    """
    if not vendor_id:
        raise BillError("The supplier has no Zoho Books vendor.")
    if not account_id:
        raise BillError("There is no expense account to book the lines to.")

    bill_number = invoice.invoice_number
    if not bill_number:
        raise BillError("The invoice has no invoice number.")
    if len(bill_number) > MAX_BILL_NUMBER_LENGTH:
        raise BillError(f"The invoice number is longer than {MAX_BILL_NUMBER_LENGTH} characters.")
    if invoice.date is None:
        raise BillError("The invoice has no date.")
    if not invoice.line_items:
        raise BillError("The invoice has no line items.")

    total = _number(invoice.totals.total)
    if total is None:
        raise BillError("The invoice has no total.")
    total = _money(total)
    if total <= 0:
        raise BillError("The invoice total must be more than zero. Credit notes cannot be sent as bills.")

    lines: list[dict] = []
    lines_sum = Decimal(0)
    for index, item in enumerate(invoice.line_items):
        line, amount = _line(index, item, account_id)
        lines.append(line)
        lines_sum += amount

    tax = _number(invoice.totals.tax)
    tax = _money(tax) if tax is not None else Decimal(0)
    if tax < 0:
        raise BillError("The tax is negative. Credit notes cannot be sent as bills.")
    if tax > 0:
        lines.append({"account_id": account_id, "description": TAX_DESCRIPTION, "quantity": 1.0, "rate": float(tax)})

    adjustment = total - lines_sum - tax
    allowed = max(Decimal(str(tolerance)) * (len(invoice.line_items) + 1), CENT)
    if abs(adjustment) > allowed:
        raise BillError(
            f"The lines and tax add up to {lines_sum + tax:,.2f}, but the invoice total is {total:,.2f}. "
            "Correct the invoice so they agree."
        )

    payload: dict = {
        "vendor_id": vendor_id,
        "bill_number": bill_number,
        "date": invoice.date.isoformat(),
        "line_items": lines,
    }
    if currency_id:
        payload["currency_id"] = currency_id
    if adjustment != 0:
        payload["adjustment"] = float(adjustment)
        payload["adjustment_description"] = ADJUSTMENT_DESCRIPTION
    if notes:
        payload["notes"] = notes[:MAX_NOTES_LENGTH]

    if payload_total(payload) != total:
        raise BillError("The bill total could not be matched to the invoice total.")
    return payload