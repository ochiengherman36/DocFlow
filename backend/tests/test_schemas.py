import datetime

import pytest

from schemas import InvoiceData, LineItem, Totals


def test_valid_full_invoice():
    inv = InvoiceData.model_validate(
        {
            "supplier": "Kijani Fuel Distributors Ltd",
            "invoice_number": "KFD-2026-0412",
            "date": "2026-10-12",
            "currency": "KES",
            "line_items": [
                {"description": "Diesel (AGO)", "quantity": 2000, "unit_price": 198.5, "amount": 397000},
            ],
            "totals": {"subtotal": 397000, "tax": 63520, "total": 460520},
        }
    )
    assert inv.supplier == "Kijani Fuel Distributors Ltd"
    assert inv.date == datetime.date(2026, 10, 12)
    assert inv.line_items[0].amount == 397000.0
    assert inv.totals.total == 460520.0


def test_empty_dict_gives_all_none():
    inv = InvoiceData.model_validate({})
    assert inv.supplier is None
    assert inv.invoice_number is None
    assert inv.date is None
    assert inv.currency is None
    assert inv.line_items == []
    assert inv.totals == Totals()


def test_partial_invoice_keeps_what_is_there():
    inv = InvoiceData.model_validate(
        {"supplier": "Twiga Auto Spares", "totals": {"total": 69048}, "line_items": [{"description": "Oil Filter"}]}
    )
    assert inv.supplier == "Twiga Auto Spares"
    assert inv.invoice_number is None
    assert inv.totals.total == 69048.0
    assert inv.totals.subtotal is None
    assert inv.line_items[0] == LineItem(description="Oil Filter")


def test_messy_values_are_cleaned():
    inv = InvoiceData.model_validate(
        {
            "invoice_number": 7781,
            "currency": "kes",
            "totals": {"total": "KES 1,234.50"},
            "line_items": [{"quantity": "40", "unit_price": "650.00"}],
        }
    )
    assert inv.invoice_number == "7781"
    assert inv.currency == "KES"
    assert inv.totals.total == 1234.5
    assert inv.line_items[0].quantity == 40.0


@pytest.mark.parametrize(
    "bad",
    [
        {"date": "12th of October"},
        {"date": 12345},
        {"currency": "Kenyan Shillings"},
        {"line_items": None},
        {"line_items": "none"},
        {"line_items": [None, 5, "x", {"amount": "n/a"}]},
        {"totals": None},
        {"totals": {"total": "unreadable"}},
        {"supplier": ["a", "b"]},
        {"unknown_field": "ignored"},
    ],
)
def test_garbage_never_raises(bad):
    inv = InvoiceData.model_validate(bad)
    assert isinstance(inv, InvoiceData)


def test_garbage_values_become_none():
    inv = InvoiceData.model_validate(
        {"date": "12th of October", "currency": "Kenyan Shillings", "totals": {"total": "unreadable"}}
    )
    assert inv.date is None
    assert inv.currency is None
    assert inv.totals.total is None


def test_json_output_is_frontend_friendly():
    data = InvoiceData.model_validate({"date": "2026-10-12", "totals": {"total": 10}}).model_dump(mode="json")
    assert data["date"] == "2026-10-12"
    assert data["totals"]["total"] == 10.0
    assert data["line_items"] == []