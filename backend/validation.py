import datetime
from dataclasses import dataclass
from typing import Sequence

from schemas import Flag, FlagCode, InvoiceData, Severity
from suppliers import match_supplier

DEFAULT_MAX_AGE_DAYS = 365
DEFAULT_TOLERANCE = 0.01 

# Currencies we expect to see; add a code here when a new market appears.
KNOWN_CURRENCIES = frozenset(
    {
        "KES", "UGX", "TZS", "RWF", "ETB", "NGN", "GHS", "ZAR", "EGP", "MAD",
        "USD", "EUR", "GBP", "AED", "INR", "CNY", "JPY", "CAD", "AUD", "CHF",
    }
)

@dataclass(frozen=True)
class DuplicateMatch:
    status: str
    filename: str
    when: datetime.datetime | None = None


def _flag(code: FlagCode, severity: Severity, field: str, message: str) -> Flag:
    return Flag(code=code, severity=severity, field=field, message=message)


def _check_missing(invoice: InvoiceData) -> list[Flag]:
    required = [
        ("supplier", "Supplier name", invoice.supplier),
        ("invoice_number", "Invoice number", invoice.invoice_number),
        ("date", "Invoice date", invoice.date),
        ("currency", "Currency", invoice.currency),
        ("totals.total", "Invoice total", invoice.totals.total),
    ]
    flags = [
        _flag(FlagCode.MISSING_FIELD, Severity.ERROR, field, f"{label} was not found on the invoice.")
        for field, label, value in required
        if value is None
    ]
    if not invoice.line_items:
        flags.append(_flag(FlagCode.NO_LINE_ITEMS, Severity.ERROR, "line_items", "No line items were found."))
    return flags


def _differs(a: float, b: float, tolerance: float) -> bool:
    # The small extra margin stops float noise from flagging a difference of exactly the tolerance.
    return abs(a - b) > tolerance + 1e-9


def _check_math(invoice: InvoiceData, tolerance: float) -> list[Flag]:
    """Check the invoice adds up. A check is skipped when a number it needs is missing."""
    flags: list[Flag] = []

    for index, item in enumerate(invoice.line_items):
        if item.quantity is None or item.unit_price is None or item.amount is None:
            continue
        expected = item.quantity * item.unit_price
        if _differs(expected, item.amount, tolerance):
            flags.append(
                _flag(
                    FlagCode.LINE_AMOUNT_MISMATCH,
                    Severity.ERROR,
                    f"line_items[{index}].amount",
                    f"{item.quantity:g} x {item.unit_price:,.2f} = {expected:,.2f}, "
                    f"but the line amount is {item.amount:,.2f}.",
                )
            )

    totals = invoice.totals
    amounts = [item.amount for item in invoice.line_items]
    if amounts and None not in amounts and totals.subtotal is not None:
        line_sum = sum(amounts)
        if _differs(line_sum, totals.subtotal, tolerance * len(amounts)):
            flags.append(
                _flag(
                    FlagCode.SUBTOTAL_MISMATCH,
                    Severity.ERROR,
                    "totals.subtotal",
                    f"The line amounts add up to {line_sum:,.2f}, but the subtotal is {totals.subtotal:,.2f}.",
                )
            )

    if totals.subtotal is not None and totals.tax is not None and totals.total is not None:
        expected_total = totals.subtotal + totals.tax
        if _differs(expected_total, totals.total, tolerance):
            flags.append(
                _flag(
                    FlagCode.TOTAL_MISMATCH,
                    Severity.ERROR,
                    "totals.total",
                    f"Subtotal {totals.subtotal:,.2f} + tax {totals.tax:,.2f} = {expected_total:,.2f}, "
                    f"but the total is {totals.total:,.2f}.",
                )
            )

    return flags

def _check_supplier(invoice: InvoiceData, known_suppliers: Sequence[str]) -> list[Flag]:
    """Compare the supplier with the stored list. Skipped when there is no name or no list."""
    if invoice.supplier is None or not known_suppliers:
        return []

    match = match_supplier(invoice.supplier, known_suppliers)
    if match.status == "near":
        return [
            _flag(
                FlagCode.SUPPLIER_NEAR_MATCH,
                Severity.WARNING,
                "supplier",
                f"'{invoice.supplier}' is very close to the stored supplier '{match.stored_name}'. "
                "Is it the same company?",
            )
        ]
    if match.status == "unknown":
        return [
            _flag(
                FlagCode.SUPPLIER_UNKNOWN,
                Severity.WARNING,
                "supplier",
                f"'{invoice.supplier}' is not in the supplier list. Is this a new supplier?",
            )
        ]
    return []

def _check_duplicates(invoice: InvoiceData, duplicates: Sequence[DuplicateMatch]) -> list[Flag]:
    if not duplicates or not invoice.invoice_number:
        return []

    approved = [match for match in duplicates if match.status == "approved"]
    first = approved[0] if approved else duplicates[0]
    when = first.when.strftime("%d %b %Y") if first.when else "an earlier date"
    who = f"Invoice {invoice.invoice_number}" + (f" from {invoice.supplier}" if invoice.supplier else "")
    more = len(duplicates) - 1
    extra = f" and {more} more like it" if more > 0 else ""

    if approved:
        severity = Severity.ERROR
        message = f"{who} was already approved on {when} (file: {first.filename}){extra}."
    else:
        severity = Severity.WARNING
        message = f"{who} was already uploaded on {when} and is awaiting review (file: {first.filename}){extra}."
    return [_flag(FlagCode.DUPLICATE_INVOICE, severity, "invoice_number", message)]

def _check_date(invoice: InvoiceData, today: datetime.date, max_age_days: int) -> list[Flag]:
    if invoice.date is None:
        return []
    if invoice.date > today:
        return [
            _flag(
                FlagCode.DATE_IN_FUTURE,
                Severity.WARNING,
                "date",
                f"The invoice date {invoice.date.isoformat()} is in the future.",
            )
        ]
    if (today - invoice.date).days > max_age_days:
        return [
            _flag(
                FlagCode.DATE_TOO_OLD,
                Severity.WARNING,
                "date",
                f"The invoice date {invoice.date.isoformat()} is more than {max_age_days} days old.",
            )
        ]
    return []


def _check_currency(invoice: InvoiceData) -> list[Flag]:
    if invoice.currency is None or invoice.currency in KNOWN_CURRENCIES:
        return []
    return [
        _flag(
            FlagCode.UNKNOWN_CURRENCY,
            Severity.WARNING,
            "currency",
            f"'{invoice.currency}' is not a currency code we recognise.",
        )
    ]


def _check_negatives(invoice: InvoiceData) -> list[Flag]:
    numbers: list[tuple[str, float | None]] = []
    for index, item in enumerate(invoice.line_items):
        numbers += [
            (f"line_items[{index}].quantity", item.quantity),
            (f"line_items[{index}].unit_price", item.unit_price),
            (f"line_items[{index}].amount", item.amount),
        ]
    numbers += [
        ("totals.subtotal", invoice.totals.subtotal),
        ("totals.tax", invoice.totals.tax),
        ("totals.total", invoice.totals.total),
    ]
    return [
        _flag(FlagCode.NEGATIVE_VALUE, Severity.WARNING, field, f"{value:,.2f} is negative. Check that this is intended.")
        for field, value in numbers
        if value is not None and value < 0
    ]


def validate(
    invoice: InvoiceData,
    *,
    today: datetime.date | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    tolerance: float = DEFAULT_TOLERANCE,
    known_suppliers: Sequence[str] = (),
    duplicates: Sequence[DuplicateMatch] = (),
) -> list[Flag]:
    """Return the problems found on an invoice. Never raises, never calls Claude."""
    today = today or datetime.date.today()
    return [
        *_check_missing(invoice),
        *_check_supplier(invoice, known_suppliers),
        *_check_duplicates(invoice, duplicates),
        *_check_math(invoice, tolerance),
        *_check_date(invoice, today, max_age_days),
        *_check_currency(invoice),
        *_check_negatives(invoice),
    ]