import datetime

from schemas import Flag, FlagCode, InvoiceData, Severity

DEFAULT_MAX_AGE_DAYS = 365

# Currencies we expect to see. I will update.
KNOWN_CURRENCIES = frozenset(
    {
        "KES", "UGX", "TZS", "RWF", "ETB", "NGN", "GHS", "ZAR", "EGP", "MAD",
        "USD", "EUR", "GBP", "AED", "INR", "CNY", "JPY", "CAD", "AUD", "CHF",
    }
)


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
) -> list[Flag]:
    """Return the problems found on an invoice. Never raises, never calls Claude."""
    today = today or datetime.date.today()
    return [
        *_check_missing(invoice),
        *_check_date(invoice, today, max_age_days),
        *_check_currency(invoice),
        *_check_negatives(invoice),
    ]