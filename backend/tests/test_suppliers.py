import datetime
import json

import pytest

from schemas import FlagCode, InvoiceData, Severity
from suppliers import (
    SupplierListError,
    load_known_suppliers,
    match_supplier,
    normalise_name,
)
from validation import validate

TODAY = datetime.date(2026, 10, 30)
KNOWN = ["Kijani Fuel Distributors Ltd", "Mwangaza Office Supplies", "Twiga Auto Spares"]


def invoice_with(supplier) -> InvoiceData:
    return InvoiceData.model_validate(
        {
            "supplier": supplier,
            "invoice_number": "1",
            "date": "2026-10-12",
            "currency": "KES",
            "line_items": [{"description": "x", "quantity": 2, "unit_price": 10, "amount": 20}],
            "totals": {"subtotal": 20, "tax": 3.2, "total": 23.2},
        }
    )


def supplier_flags(supplier, known=KNOWN):
    flags = validate(invoice_with(supplier), today=TODAY, known_suppliers=known)
    return [(f.code, f.severity, f.field) for f in flags]


def test_exact_match():
    match = match_supplier("Kijani Fuel Distributors Ltd", KNOWN)
    assert match.status == "exact"
    assert match.stored_name == "Kijani Fuel Distributors Ltd"


@pytest.mark.parametrize(
    "written",
    [
        "kijani fuel distributors ltd",
        "KIJANI FUEL DISTRIBUTORS LTD",
        "Kijani Fuel Distributors Limited",
        "Kijani Fuel Distributors",
        "Kijani Fuel Distributors Ltd.",
        "  Kijani   Fuel  Distributors Ltd ",
    ],
)
def test_trivial_differences_still_count_as_exact(written):
    assert match_supplier(written, KNOWN).status == "exact"


def test_misspelling_is_a_near_match_and_names_the_stored_supplier():
    match = match_supplier("Kijani Fuel Distibutors Ltd", KNOWN)
    assert match.status == "near"
    assert match.stored_name == "Kijani Fuel Distributors Ltd"
    assert match.score >= 85


def test_reordered_words_are_a_near_match():
    match = match_supplier("Distributors Fuel Kijani", KNOWN)
    assert match.status in {"exact", "near"}
    assert match.stored_name == "Kijani Fuel Distributors Ltd"


def test_completely_new_name_is_unknown():
    match = match_supplier("Savannah Hardware Wholesalers", KNOWN)
    assert match.status == "unknown"


def test_similar_but_different_company_is_unknown():
    assert match_supplier("Twiga Foods Kenya", KNOWN).status == "unknown"


def test_empty_list_is_unknown():
    assert match_supplier("Anything Ltd", []).status == "unknown"


def test_threshold_can_be_changed():
    assert match_supplier("Kijani Fuel Distibutors Ltd", KNOWN, threshold=99).status == "unknown"


def test_normalise_name():
    assert normalise_name("  Kijani & Sons Ltd. ") == "kijani sons"
    assert normalise_name("Ltd") == "ltd"


def test_exact_supplier_raises_no_flag():
    assert supplier_flags("Kijani Fuel Distributors Ltd") == []


def test_misspelled_supplier_raises_near_match_warning():
    assert supplier_flags("Kijani Fuel Distibutors Ltd") == [
        (FlagCode.SUPPLIER_NEAR_MATCH, Severity.WARNING, "supplier")
    ]


def test_near_match_message_suggests_the_stored_name():
    flags = validate(invoice_with("Kijani Fuel Distibutors Ltd"), today=TODAY, known_suppliers=KNOWN)
    assert "Kijani Fuel Distributors Ltd" in flags[0].message
    assert "Kijani Fuel Distibutors Ltd" in flags[0].message


def test_new_supplier_raises_unknown_warning():
    assert supplier_flags("Savannah Hardware Wholesalers") == [
        (FlagCode.SUPPLIER_UNKNOWN, Severity.WARNING, "supplier")
    ]


def test_empty_list_switches_the_check_off():
    assert supplier_flags("Savannah Hardware Wholesalers", known=[]) == []


def test_no_list_given_switches_the_check_off():
    assert validate(invoice_with("Anyone"), today=TODAY) == []


def test_missing_supplier_gets_only_the_missing_flag():
    flags = validate(invoice_with(None), today=TODAY, known_suppliers=KNOWN)
    assert [(f.code, f.field) for f in flags] == [(FlagCode.MISSING_FIELD, "supplier")]


def test_load_reads_a_json_list(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(["A Ltd", "B Ltd"]), encoding="utf-8")
    assert load_known_suppliers(path) == ["A Ltd", "B Ltd"]


def test_load_trims_blanks_and_duplicates(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(["  A Ltd ", "", "   ", "A Ltd", "B Ltd"]), encoding="utf-8")
    assert load_known_suppliers(path) == ["A Ltd", "B Ltd"]


def test_load_accepts_an_empty_list(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("[]", encoding="utf-8")
    assert load_known_suppliers(path) == []


def test_load_missing_file_fails_loudly(tmp_path):
    with pytest.raises(SupplierListError, match="not found"):
        load_known_suppliers(tmp_path / "nope.json")


@pytest.mark.parametrize("content", ["not json", '{"a": 1}', '["ok", 5]', '"just a string"'])
def test_load_bad_format_fails_loudly(tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SupplierListError):
        load_known_suppliers(path)


def test_the_shipped_list_loads_and_has_the_sample_suppliers():
    names = load_known_suppliers()
    assert "Kijani Fuel Distributors Ltd" in names
    assert "Mwangaza Office Supplies" in names
    assert "Twiga Auto Spares" in names