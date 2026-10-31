import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from schemas import InvoiceData

SAMPLES = Path(__file__).parent.parent / "samples"
EXPECTED = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
EXPECTED_FLAGS = json.loads((SAMPLES / "expected_flags.json").read_text(encoding="utf-8"))

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)


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


def flag_keys(flags: list[dict]) -> set[tuple[str, str, str]]:
    return {(f["code"], f["severity"], f["field"]) for f in flags}


CLEAN = {
    "supplier": "Kijani Fuel Distributors Ltd",
    "invoice_number": "KFD-1",
    "date": "2026-10-12",
    "currency": "KES",
    "line_items": [{"description": "Diesel", "quantity": 2, "unit_price": 10, "amount": 20}],
    "totals": {"subtotal": 20, "tax": 3.2, "total": 23.2},
}


def test_validate_clean_invoice_has_no_flags():
    r = client.post("/validate", json=CLEAN)
    assert r.status_code == 200
    assert r.json() == {"flags": []}


def test_validate_total_flag_appears_and_disappears_when_edited():
    wrong = {**CLEAN, "totals": {"subtotal": 20, "tax": 3.2, "total": 24.2}}
    r = client.post("/validate", json=wrong)
    assert flag_keys(r.json()["flags"]) == {("total_mismatch", "error", "totals.total")}

    fixed = {**CLEAN, "totals": {"subtotal": 20, "tax": 3.2, "total": 23.2}}
    r = client.post("/validate", json=fixed)
    assert r.json()["flags"] == []


def test_validate_accepts_text_numbers_as_typed_in_a_form():
    typed = {
        **CLEAN,
        "line_items": [{"description": "Diesel", "quantity": "2", "unit_price": "10.00", "amount": "20.00"}],
        "totals": {"subtotal": "20.00", "tax": "3.20", "total": "KES 23.20"},
    }
    r = client.post("/validate", json=typed)
    assert r.status_code == 200
    assert r.json()["flags"] == []


def test_validate_supplier_near_match():
    r = client.post("/validate", json={**CLEAN, "supplier": "Kijani Fuel Distibutors Ltd"})
    assert flag_keys(r.json()["flags"]) == {("supplier_near_match", "warning", "supplier")}


def test_validate_new_supplier():
    r = client.post("/validate", json={**CLEAN, "supplier": "Savannah Hardware Wholesalers"})
    assert flag_keys(r.json()["flags"]) == {("supplier_unknown", "warning", "supplier")}


def test_validate_empty_object_reports_every_missing_field():
    r = client.post("/validate", json={})
    assert r.status_code == 200
    codes = [(f["code"], f["field"]) for f in r.json()["flags"]]
    assert ("missing_field", "supplier") in codes
    assert ("missing_field", "date") in codes
    assert ("no_line_items", "line_items") in codes


def test_validate_flag_shape():
    r = client.post("/validate", json={**CLEAN, "totals": {"subtotal": 20, "tax": 3.2, "total": 99}})
    flag = r.json()["flags"][0]
    assert set(flag) == {"code", "severity", "field", "message"}
    assert "99.00" in flag["message"]


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '"text"'])
def test_validate_rejects_a_body_that_is_not_an_object(body):
    r = client.post("/validate", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422


@pytest.mark.parametrize("name", list(EXPECTED))
def test_extract_returns_the_expected_flags_for_each_sample(monkeypatch, name):
    """Claude is faked with the correct data for each sample, so this checks the API wiring for free."""
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: invoice_from_expected(EXPECTED[name]))
    pdf = (SAMPLES / name).read_bytes()

    r = client.post("/extract", files={"file": (name, pdf, "application/pdf")})

    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == name
    want = {(e["code"], e["severity"], e["field"]) for e in EXPECTED_FLAGS[name]}
    assert flag_keys(body["flags"]) == want


def test_extract_and_validate_agree(monkeypatch):
    """The flags /extract returns are the same ones /validate gives for the same invoice."""
    name = "invoice_wrong_total.pdf"
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: invoice_from_expected(EXPECTED[name]))
    extracted = client.post("/extract", files={"file": (name, (SAMPLES / name).read_bytes(), "application/pdf")}).json()

    revalidated = client.post(
        "/validate", json=extracted["invoice"], params={"document_id": extracted["document_id"]}
    ).json()
    assert revalidated["flags"] == extracted["flags"]


def test_new_settings_have_sensible_defaults():
    assert main.settings.validation_tolerance == 0.01