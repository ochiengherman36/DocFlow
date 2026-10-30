import datetime
import json
from pathlib import Path

import pytest

from schemas import FlagCode, InvoiceData, Severity
from validation import validate
from suppliers import load_known_suppliers

SAMPLES = Path(__file__).parent.parent / "samples"
TODAY = datetime.date(2026, 10, 30)


def make_invoice(**changes) -> InvoiceData:
    data = {
        "supplier": "Kijani Fuel Distributors Ltd",
        "invoice_number": "KFD-2026-0412",
        "date": "2026-10-12",
        "currency": "KES",
        "line_items": [{"description": "Diesel", "quantity": 2, "unit_price": 10, "amount": 20}],
        "totals": {"subtotal": 20, "tax": 3.2, "total": 23.2},
    }
    data.update(changes)
    return InvoiceData.model_validate(data)


def codes(flags):
    return [(f.code, f.field) for f in flags]

def only(flags, code):
    """Keep only the flags of one kind, so a test is not affected by unrelated checks."""
    return [f for f in flags if f.code is code]

def test_clean_invoice_raises_no_flags():
    assert validate(make_invoice(), today=TODAY) == []


@pytest.mark.parametrize(
    "field, changes",
    [
        ("supplier", {"supplier": None}),
        ("invoice_number", {"invoice_number": None}),
        ("date", {"date": None}),
        ("currency", {"currency": None}),
        ("totals.total", {"totals": {"subtotal": 20, "tax": 3.2, "total": None}}),
    ],
)
def test_missing_field_is_an_error(field, changes):
    flags = validate(make_invoice(**changes), today=TODAY)
    assert codes(flags) == [(FlagCode.MISSING_FIELD, field)]
    assert flags[0].severity is Severity.ERROR


def test_no_line_items_is_an_error():
    flags = validate(make_invoice(line_items=[]), today=TODAY)
    assert codes(flags) == [(FlagCode.NO_LINE_ITEMS, "line_items")]
    assert flags[0].severity is Severity.ERROR


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"line_items": [{"quantity": -1, "unit_price": 10, "amount": 20}]}, "line_items[0].quantity"),
        ({"line_items": [{"quantity": 2, "unit_price": -10, "amount": 20}]}, "line_items[0].unit_price"),
        ({"line_items": [{"quantity": 2, "unit_price": 10, "amount": -20}]}, "line_items[0].amount"),
        ({"totals": {"subtotal": -20, "tax": 3.2, "total": 23.2}}, "totals.subtotal"),
        ({"totals": {"subtotal": 20, "tax": -3.2, "total": 23.2}}, "totals.tax"),
        ({"totals": {"subtotal": 20, "tax": 3.2, "total": -23.2}}, "totals.total"),
    ],
)
def test_negative_amount_is_a_warning(changes, field):
    flags = only(validate(make_invoice(**changes), today=TODAY), FlagCode.NEGATIVE_VALUE)
    assert codes(flags) == [(FlagCode.NEGATIVE_VALUE, field)]
    assert flags[0].severity is Severity.WARNING

def test_negative_flag_points_at_the_right_line():
    items = [
        {"quantity": 1, "unit_price": 5, "amount": 5},
        {"quantity": 1, "unit_price": 5, "amount": 5},
        {"quantity": 1, "unit_price": 5, "amount": -5},
    ]
    flags = only(validate(make_invoice(line_items=items), today=TODAY), FlagCode.NEGATIVE_VALUE)
    assert codes(flags) == [(FlagCode.NEGATIVE_VALUE, "line_items[2].amount")]


def test_future_date_is_a_warning():
    flags = validate(make_invoice(date="2026-10-31"), today=TODAY)
    assert codes(flags) == [(FlagCode.DATE_IN_FUTURE, "date")]
    assert flags[0].severity is Severity.WARNING


def test_todays_date_is_fine():
    assert validate(make_invoice(date="2026-10-30"), today=TODAY) == []


def test_old_date_is_a_warning():
    too_old = TODAY - datetime.timedelta(days=366)
    flags = validate(make_invoice(date=too_old.isoformat()), today=TODAY)
    assert codes(flags) == [(FlagCode.DATE_TOO_OLD, "date")]
    assert flags[0].severity is Severity.WARNING


def test_date_exactly_at_the_limit_is_fine():
    at_limit = TODAY - datetime.timedelta(days=365)
    assert validate(make_invoice(date=at_limit.isoformat()), today=TODAY) == []


def test_age_limit_can_be_changed():
    ninety_one_days_ago = TODAY - datetime.timedelta(days=91)
    flags = validate(make_invoice(date=ninety_one_days_ago.isoformat()), today=TODAY, max_age_days=90)
    assert codes(flags) == [(FlagCode.DATE_TOO_OLD, "date")]


@pytest.mark.parametrize("code", ["KSH", "XYZ"])
def test_unknown_currency_is_a_warning(code):
    flags = validate(make_invoice(currency=code), today=TODAY)
    assert codes(flags) == [(FlagCode.UNKNOWN_CURRENCY, "currency")]
    assert flags[0].severity is Severity.WARNING


@pytest.mark.parametrize("code", ["KES", "USD", "eur"])
def test_known_currency_is_fine(code):
    assert validate(make_invoice(currency=code), today=TODAY) == []


def test_empty_invoice_does_not_crash_and_skips_checks_on_missing_values():
    flags = validate(InvoiceData(), today=TODAY)
    assert codes(flags) == [
        (FlagCode.MISSING_FIELD, "supplier"),
        (FlagCode.MISSING_FIELD, "invoice_number"),
        (FlagCode.MISSING_FIELD, "date"),
        (FlagCode.MISSING_FIELD, "currency"),
        (FlagCode.MISSING_FIELD, "totals.total"),
        (FlagCode.NO_LINE_ITEMS, "line_items"),
    ]


def test_several_problems_are_all_reported():
    flags = validate(make_invoice(supplier=None, date="2026-12-01", currency="KSH"), today=TODAY)
    assert codes(flags) == [
        (FlagCode.MISSING_FIELD, "supplier"),
        (FlagCode.DATE_IN_FUTURE, "date"),
        (FlagCode.UNKNOWN_CURRENCY, "currency"),
    ]


def invoice_from_expected(entry: dict) -> InvoiceData:
    return InvoiceData.model_validate(
        {
            "supplier": entry["supplier"],
            "invoice_number": entry["invoice_number"],
            "date": entry["date"],
            "currency": entry["currency"],
            "line_items": entry["line_items"],
            "totals": {"subtotal": entry["subtotal"], "tax": entry["tax"], "total": entry["total"]},
        }
    )


EXPECTED = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
EXPECTED_FLAGS = json.loads((SAMPLES / "expected_flags.json").read_text(encoding="utf-8"))
IMPLEMENTED_CODES = set(FlagCode) - {FlagCode.SUPPLIER_UNKNOWN, FlagCode.SUPPLIER_NEAR_MATCH}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_samples_raise_exactly_the_expected_flags(name):
    """For each sample, the flags raised must equal the ones listed in expected_flags.json."""
    result = validate(
        invoice_from_expected(EXPECTED[name]),
        today=TODAY,
        known_suppliers=load_known_suppliers(),
    )
    got = {(f.code.value, f.severity.value, f.field) for f in result}
    want = {(e["code"], e["severity"], e["field"]) for e in EXPECTED_FLAGS[name]}
    assert got == want