import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError

import main
from models import Supplier
from schemas import InvoiceData
from supplier_store import add_supplier

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)

SAMPLES = Path(__file__).parent.parent / "samples"
EXPECTED = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))


def body(supplier: str) -> dict:
    return {
        "supplier": supplier,
        "invoice_number": "INV-1",
        "date": "2026-10-12",
        "currency": "KES",
        "line_items": [{"description": "Item", "quantity": 2, "unit_price": 10, "amount": 20}],
        "totals": {"subtotal": 20, "tax": 3.2, "total": 23.2},
    }


def flags(supplier: str) -> list[tuple[str, str]]:
    r = client.post("/validate", json=body(supplier))
    assert r.status_code == 200
    return [(f["code"], f["field"]) for f in r.json()["flags"]]


def test_a_seeded_supplier_is_recognised():
    assert flags("Kijani Fuel Distributors Ltd") == []


def test_a_supplier_that_is_not_in_the_table_is_flagged_as_new():
    assert flags("Savannah Hardware Wholesalers") == [("supplier_unknown", "supplier")]


def test_a_new_row_in_the_table_is_used_by_the_matching_straight_away(database):
    assert flags("Savannah Hardware Wholesalers") == [("supplier_unknown", "supplier")]

    with database() as session:
        add_supplier(session, "Savannah Hardware Wholesalers")
        session.commit()

    assert flags("Savannah Hardware Wholesalers") == []


def test_a_misspelling_of_a_newly_added_supplier_names_it(database):
    with database() as session:
        add_supplier(session, "Savannah Hardware Wholesalers")
        session.commit()

    r = client.post("/validate", json=body("Savanah Hardware Wholesalers"))
    flag = r.json()["flags"][0]
    assert flag["code"] == "supplier_near_match"
    assert "'Savannah Hardware Wholesalers'" in flag["message"]


def test_a_removed_row_is_forgotten(database):
    with database() as session:
        session.execute(delete(Supplier).where(Supplier.name == "Twiga Auto Spares"))
        session.commit()
    assert flags("Twiga Auto Spares") == [("supplier_unknown", "supplier")]


def test_an_empty_table_switches_the_supplier_check_off(database):
    with database() as session:
        session.execute(delete(Supplier))
        session.commit()
    assert flags("Savannah Hardware Wholesalers") == []


def test_extract_uses_the_table_as_well(monkeypatch, database):
    entry = EXPECTED["invoice_misspelled_supplier.pdf"]
    invoice = InvoiceData.model_validate(
        {
            "supplier": entry["supplier"], "invoice_number": entry["invoice_number"], "date": entry["date"],
            "currency": entry["currency"], "line_items": entry["line_items"],
            "totals": {"subtotal": entry["subtotal"], "tax": entry["tax"], "total": entry["total"]},
        }
    )
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: invoice)
    pdf = (SAMPLES / "invoice_misspelled_supplier.pdf").read_bytes()

    def codes():
        r = client.post("/extract", files={"file": ("x.pdf", pdf, "application/pdf")})
        assert r.status_code == 200
        return [f["code"] for f in r.json()["flags"] if f["code"] != "duplicate_file"]


    assert codes() == ["supplier_near_match"]

    with database() as session:
        add_supplier(session, "Kijani Fuel Distibutors Ltd")
        session.commit()
    assert codes() == []


def test_when_the_database_is_down_the_answer_is_a_clear_503(monkeypatch):
    def broken(session):
        raise OperationalError("SELECT name FROM suppliers", {}, Exception("connection refused"))

    monkeypatch.setattr(main, "list_supplier_names", broken)
    r = client.post("/validate", json=body("Anyone"))
    assert r.status_code == 503
    assert r.json() == {"detail": "The database is not available. Please try again in a moment."}


def test_the_503_still_carries_cors_headers_so_the_page_can_read_it(monkeypatch):
    def broken(session):
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr(main, "list_supplier_names", broken)
    r = client.post("/validate", json=body("Anyone"), headers={"Origin": main.settings.frontend_origin})
    assert r.status_code == 503
    assert r.headers["access-control-allow-origin"] == main.settings.frontend_origin


def test_checks_that_do_not_need_suppliers_do_not_touch_the_database(monkeypatch):
    def broken(session):
        raise AssertionError("the database must not be queried")

    monkeypatch.setattr(main, "list_supplier_names", broken)
    assert client.get("/health").status_code == 200
    assert client.post("/extract").status_code == 400