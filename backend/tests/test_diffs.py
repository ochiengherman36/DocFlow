import copy

import pytest

from diffs import FieldChange, diff_invoice

BASE = {
    "supplier": "Kijani Fuel Distributors Ltd",
    "invoice_number": "KFD-2026-0412",
    "date": "2026-10-12",
    "currency": "KES",
    "line_items": [
        {"description": "Diesel (AGO)", "quantity": 2000, "unit_price": 198.5, "amount": 397000},
        {"description": "Delivery charge", "quantity": 1, "unit_price": 3500, "amount": 3500},
    ],
    "totals": {"subtotal": 400500, "tax": 64080, "total": 464580},
}


def changed(**path_to_value):
    new = copy.deepcopy(BASE)
    for path, value in path_to_value.items():
        target = new
        parts = path.split("__")
        for part in parts[:-1]:
            target = target[int(part)] if part.isdigit() else target[part]
        last = parts[-1]
        if last.isdigit():
            target[int(last)] = value
        else:
            target[last] = value
    return new


def test_identical_invoices_have_no_changes():
    assert diff_invoice(BASE, copy.deepcopy(BASE)) == []


@pytest.mark.parametrize(
    "field, new_value",
    [
        ("supplier", "Twiga Auto Spares"),
        ("invoice_number", "KFD-2026-9999"),
        ("date", "2026-11-01"),
        ("currency", "USD"),
    ],
)
def test_each_header_field_is_reported(field, new_value):
    assert diff_invoice(BASE, changed(**{field: new_value})) == [FieldChange(field, BASE[field], new_value)]


@pytest.mark.parametrize("field", ["subtotal", "tax", "total"])
def test_each_total_is_reported_with_its_path(field):
    assert diff_invoice(BASE, changed(**{f"totals__{field}": 1.0})) == [
        FieldChange(f"totals.{field}", BASE["totals"][field], 1.0)
    ]


@pytest.mark.parametrize("field", ["description", "quantity", "unit_price", "amount"])
def test_each_line_field_is_reported_with_its_index(field):
    new = changed(**{f"line_items__1__{field}": "x" if field == "description" else 9})
    assert diff_invoice(BASE, new) == [
        FieldChange(f"line_items[1].{field}", BASE["line_items"][1][field], "x" if field == "description" else 9)
    ]


def test_a_whole_number_and_the_same_decimal_are_the_same_value():
    assert diff_invoice(BASE, changed(totals__total=464580.0)) == []
    assert diff_invoice(BASE, changed(line_items__0__quantity=2000.0)) == []


def test_a_difference_in_the_noise_is_ignored():
    assert diff_invoice(BASE, changed(totals__tax=64080.0000000001)) == []


def test_a_real_difference_of_a_cent_is_kept():
    assert len(diff_invoice(BASE, changed(totals__tax=64080.01))) == 1


def test_clearing_a_value_is_a_change_to_nothing():
    assert diff_invoice(BASE, changed(date=None)) == [FieldChange("date", "2026-10-12", None)]


def test_filling_in_a_missing_value_is_a_change_from_nothing():
    old = changed(date=None)
    assert diff_invoice(old, BASE) == [FieldChange("date", None, "2026-10-12")]


def test_zero_is_a_value_not_nothing():
    assert diff_invoice(changed(totals__tax=None), changed(totals__tax=0)) == [FieldChange("totals.tax", None, 0)]
    assert diff_invoice(changed(totals__tax=0), changed(totals__tax=0.0)) == []


def test_an_added_line_is_reported_as_a_whole():
    new = copy.deepcopy(BASE)
    new["line_items"].append({"description": "Toll", "quantity": 1, "unit_price": 100, "amount": 100})
    assert diff_invoice(BASE, new) == [
        FieldChange("line_items[2]", None, {"description": "Toll", "quantity": 1, "unit_price": 100, "amount": 100})
    ]


def test_a_removed_line_is_reported_as_a_whole():
    new = copy.deepcopy(BASE)
    removed = new["line_items"].pop()
    assert diff_invoice(BASE, new) == [FieldChange("line_items[1]", removed, None)]


def test_changes_come_in_page_order_header_then_lines_then_totals():
    new = changed(totals__total=1.0, line_items__0__amount=5, supplier="Z", date="2026-01-01")
    assert [c.field for c in diff_invoice(BASE, new)] == ["supplier", "date", "line_items[0].amount", "totals.total"]


def test_missing_sections_in_the_old_data_are_treated_as_empty():
    assert diff_invoice({}, {"supplier": "A"}) == [FieldChange("supplier", None, "A")]
    assert diff_invoice({"supplier": "A", "line_items": None, "totals": None}, {"supplier": "A"}) == []


def test_text_is_compared_exactly():
    assert diff_invoice(BASE, changed(supplier="kijani fuel distributors ltd")) != []