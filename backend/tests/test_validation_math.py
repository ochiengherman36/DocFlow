import datetime

import pytest

from schemas import FlagCode, InvoiceData, Severity
from validation import validate

TODAY = datetime.date(2026, 10, 30)


def invoice(items, subtotal, tax, total) -> InvoiceData:
    """A complete invoice, so only the maths can raise flags."""
    return InvoiceData.model_validate(
        {
            "supplier": "Kijani Fuel Distributors Ltd",
            "invoice_number": "KFD-1",
            "date": "2026-10-12",
            "currency": "KES",
            "line_items": [
                {"description": "Item", "quantity": q, "unit_price": p, "amount": a} for q, p, a in items
            ],
            "totals": {"subtotal": subtotal, "tax": tax, "total": total},
        }
    )


def flags_for(inv: InvoiceData, **kwargs):
    return [(f.code, f.field) for f in validate(inv, today=TODAY, **kwargs)]


def test_consistent_invoice_has_no_flags():
    assert flags_for(invoice([(2, 10, 20), (1, 5, 5)], 25, 4, 29)) == []


def test_line_amount_mismatch():
    inv = invoice([(2, 10, 25)], 25, 4, 29)
    flags = validate(inv, today=TODAY)
    assert [(f.code, f.field) for f in flags] == [(FlagCode.LINE_AMOUNT_MISMATCH, "line_items[0].amount")]
    assert flags[0].severity is Severity.ERROR
    assert "2 x 10.00 = 20.00" in flags[0].message
    assert "25.00" in flags[0].message


def test_line_mismatch_points_at_the_right_line():
    inv = invoice([(1, 5, 5), (1, 5, 5), (2, 10, 99)], 109, 17.44, 126.44)
    assert flags_for(inv) == [(FlagCode.LINE_AMOUNT_MISMATCH, "line_items[2].amount")]


def test_subtotal_mismatch():
    inv = invoice([(2, 10, 20)], 30, 4.8, 34.8)
    flags = validate(inv, today=TODAY)
    assert [(f.code, f.field) for f in flags] == [(FlagCode.SUBTOTAL_MISMATCH, "totals.subtotal")]
    assert flags[0].severity is Severity.ERROR
    assert "20.00" in flags[0].message and "30.00" in flags[0].message


def test_total_mismatch():
    inv = invoice([(2, 10, 20)], 20, 3.2, 24.2)
    flags = validate(inv, today=TODAY)
    assert [(f.code, f.field) for f in flags] == [(FlagCode.TOTAL_MISMATCH, "totals.total")]
    assert flags[0].severity is Severity.ERROR
    assert "23.20" in flags[0].message and "24.20" in flags[0].message


@pytest.mark.parametrize(
    "inv",
    [
        # unit price shown to two decimals while the amount was calculated on more
        invoice([(3, 33.333, 100.00)], 100.00, 16.00, 116.00),
        # three amounts that each round a little, adding to a total a fraction off
        invoice([(1, 0.333, 0.333), (1, 0.333, 0.333), (1, 0.333, 0.333)], 1.00, 0.16, 1.16),
        # exactly one cent off on a line, and on the total
        invoice([(2, 10, 20.01)], 20.01, 3.2, 23.21),
        invoice([(2, 10, 20)], 20, 3.2, 23.21),
    ],
)
def test_rounding_differences_do_not_flag(inv):
    assert flags_for(inv) == []


def test_two_cents_off_on_a_line_does_flag():
    assert flags_for(invoice([(2, 10, 20.02)], 20.02, 3.2, 23.22)) == [
        (FlagCode.LINE_AMOUNT_MISMATCH, "line_items[0].amount")
    ]


def test_subtotal_allowance_grows_with_the_number_of_lines():
    three_lines = [(1, 1, 1.00), (1, 1, 1.00), (1, 1, 1.00)]
    assert flags_for(invoice(three_lines, 3.03, 0.48, 3.51)) == []
    assert flags_for(invoice(three_lines, 3.05, 0.48, 3.53)) == [(FlagCode.SUBTOTAL_MISMATCH, "totals.subtotal")]


def test_tolerance_can_be_changed():
    inv = invoice([(2, 10, 20)], 20, 3.2, 23.7)
    assert flags_for(inv) == [(FlagCode.TOTAL_MISMATCH, "totals.total")]
    assert flags_for(inv, tolerance=1.0) == []


def test_several_mismatches_are_all_reported():
    inv = invoice([(2, 10, 25)], 30, 4.8, 99)
    assert flags_for(inv) == [
        (FlagCode.LINE_AMOUNT_MISMATCH, "line_items[0].amount"),
        (FlagCode.SUBTOTAL_MISMATCH, "totals.subtotal"),
        (FlagCode.TOTAL_MISMATCH, "totals.total"),
    ]


def partial_invoice(items, subtotal, tax, total) -> InvoiceData:
    return InvoiceData.model_validate(
        {
            "supplier": "S",
            "invoice_number": "1",
            "date": "2026-10-12",
            "currency": "KES",
            "line_items": items,
            "totals": {"subtotal": subtotal, "tax": tax, "total": total},
        }
    )


@pytest.mark.parametrize(
    "inv",
    [
        # a line missing its quantity: the line check is skipped
        partial_invoice([{"unit_price": 10, "amount": 25}], 25, 4, 29),
        partial_invoice([{"quantity": 2, "amount": 25}], 25, 4, 29),
        # a line missing its amount: the line check and the subtotal check are skipped
        partial_invoice([{"quantity": 2, "unit_price": 10}], 999, 159.84, 1158.84),
        partial_invoice([{"quantity": 2, "unit_price": 10, "amount": 20}, {"description": "x"}], 999, 159.84, 1158.84),
        # no subtotal: neither the subtotal nor the total check can run
        partial_invoice([{"quantity": 2, "unit_price": 10, "amount": 20}], None, 4, 999),
        # no tax: the total check is skipped
        partial_invoice([{"quantity": 2, "unit_price": 10, "amount": 20}], 20, None, 999),
        # no total: nothing to compare
        partial_invoice([{"quantity": 2, "unit_price": 10, "amount": 20}], 20, 3.2, None),
        # no line items: the subtotal check has nothing to add up
        partial_invoice([], 20, 3.2, 23.2),
    ],
)
def test_missing_numbers_skip_their_check_and_do_not_crash(inv):
    math_codes = {FlagCode.LINE_AMOUNT_MISMATCH, FlagCode.SUBTOTAL_MISMATCH, FlagCode.TOTAL_MISMATCH}
    assert [f for f in validate(inv, today=TODAY) if f.code in math_codes] == []


def test_a_zero_value_is_a_number_not_a_missing_one():
    inv = partial_invoice([{"quantity": 2, "unit_price": 10, "amount": 0}], 0, 0, 0)
    assert flags_for(inv) == [(FlagCode.LINE_AMOUNT_MISMATCH, "line_items[0].amount")]