import copy
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

import main
from models import AuditEntry, Document, Extraction
from schemas import InvoiceData

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)

SAMPLES = Path(__file__).parent.parent / "samples"
EXPECTED = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))


def invoice_of(name: str) -> InvoiceData:
    e = EXPECTED[name]
    return InvoiceData.model_validate(
        {
            "supplier": e["supplier"], "invoice_number": e["invoice_number"], "date": e["date"],
            "currency": e["currency"], "line_items": e["line_items"],
            "totals": {"subtotal": e["subtotal"], "tax": e["tax"], "total": e["total"]},
        }
    )


DIGITAL = invoice_of("invoice_digital.pdf")
ORIGINAL = DIGITAL.model_dump(mode="json")


@pytest.fixture
def doc(monkeypatch):
    """An uploaded, pending document (the digital invoice). Claude is faked."""
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: DIGITAL)
    pdf = (SAMPLES / "invoice_digital.pdf").read_bytes()
    r = client.post("/extract", files={"file": ("invoice.pdf", pdf, "application/pdf")})
    assert r.status_code == 200
    return r.json()["document_id"]


def edited(**changes) -> dict:
    data = copy.deepcopy(ORIGINAL)
    for key, value in changes.items():
        if key.startswith("totals_"):
            data["totals"][key.removeprefix("totals_")] = value
        elif key.startswith("line"):
            index, field = key.removeprefix("line").split("_", 1)
            data["line_items"][int(index)][field] = value
        else:
            data[key] = value
    return data


def save(document_id, body, **headers):
    return client.put(f"/documents/{document_id}", json=body, headers=headers)


def audit(database, document_id) -> list[AuditEntry]:
    with database() as session:
        return list(session.scalars(select(AuditEntry).where(AuditEntry.document_id == uuid.UUID(document_id)).order_by(AuditEntry.id)))


def stored(database, document_id) -> Document:
    with database() as session:
        return session.get(Document, uuid.UUID(document_id))


def test_editing_a_total_stores_it_and_the_log_shows_from_and_to(database, doc):
    r = save(doc, edited(totals_total=587000), **{"X-Reviewer": "Herman"})

    assert r.status_code == 200
    assert r.json()["changes"] == [{"field": "totals.total", "from": 587975, "to": 587000}]
    assert r.json()["invoice"]["totals"]["total"] == 587000

    assert stored(database, doc).current_data["totals"]["total"] == 587000
    entries = audit(database, doc)
    assert [e.action for e in entries] == ["extracted", "edited"]
    edit = entries[1]
    assert edit.actor == "Herman"
    assert edit.details == {"changes": {"totals.total": {"from": 587975, "to": 587000}}}
    assert edit.created_at is not None


def test_the_stored_extraction_is_unchanged(database, doc):
    save(doc, edited(totals_total=587000, supplier="Someone Else", line0_amount=1))
    with database() as session:
        extraction = session.scalars(select(Extraction)).one()
        assert extraction.data["totals"]["total"] == 587975
        assert extraction.data["supplier"] == "Kijani Fuel Distributors Ltd"
        assert extraction.data["line_items"][0]["amount"] == 397000
        assert extraction.data == ORIGINAL


def test_the_edit_entry_points_at_the_original_extraction(database, doc):
    save(doc, edited(totals_total=1))
    with database() as session:
        extraction = session.scalars(select(Extraction)).one()
    assert audit(database, doc)[1].extraction_id == extraction.id


def test_the_response_is_checked_against_the_new_data(doc):
    r = save(doc, edited(totals_total=587000))
    assert [f["code"] for f in r.json()["flags"]] == ["total_mismatch"]
    assert "587,000.00" in r.json()["flags"][0]["message"]


def test_saving_unchanged_data_logs_nothing(database, doc):
    before = stored(database, doc)
    r = save(doc, ORIGINAL)

    assert r.status_code == 200
    assert r.json()["changes"] == []
    assert [e.action for e in audit(database, doc)] == ["extracted"]
    after = stored(database, doc)
    assert after.current_data == before.current_data
    assert after.updated_at == before.updated_at


def test_saving_the_same_edit_twice_logs_it_once(database, doc):
    save(doc, edited(totals_total=587000))
    second = save(doc, edited(totals_total=587000))
    assert second.json()["changes"] == []
    assert [e.action for e in audit(database, doc)] == ["extracted", "edited"]


def test_numbers_typed_as_text_do_not_count_as_changes(database, doc):
    typed = edited()
    typed["totals"] = {"subtotal": "506,875.00", "tax": "81,100.00", "total": "587975"}
    typed["line_items"][0]["quantity"] = "2000"
    r = save(doc, typed)
    assert r.json()["changes"] == []
    assert [e.action for e in audit(database, doc)] == ["extracted"]


def test_text_typed_into_the_form_is_stored_as_a_number(database, doc):
    typed = edited()
    typed["totals"]["total"] = "KES 587,000.00"
    r = save(doc, typed)
    assert r.json()["changes"] == [{"field": "totals.total", "from": 587975, "to": 587000}]
    assert stored(database, doc).current_data["totals"]["total"] == 587000


def test_every_changed_field_is_logged_in_one_entry(database, doc):
    save(doc, edited(supplier="Twiga Auto Spares", date="2026-11-01", line0_amount=396000, totals_total=586000))
    entries = audit(database, doc)
    assert [e.action for e in entries] == ["extracted", "edited"]
    assert entries[1].details["changes"] == {
        "supplier": {"from": "Kijani Fuel Distributors Ltd", "to": "Twiga Auto Spares"},
        "date": {"from": "2026-10-12", "to": "2026-11-01"},
        "line_items[0].amount": {"from": 397000, "to": 396000},
        "totals.total": {"from": 587975, "to": 586000},
    }


def test_the_changes_in_the_answer_are_in_page_order(doc):
    r = save(doc, edited(totals_total=1, line1_amount=2, supplier="Z"))
    assert [c["field"] for c in r.json()["changes"]] == ["supplier", "line_items[1].amount", "totals.total"]


def test_each_save_logs_the_change_from_the_previous_save_not_the_original(database, doc):
    save(doc, edited(totals_total=587000))
    save(doc, edited(totals_total=586000))
    entries = audit(database, doc)
    assert entries[1].details["changes"]["totals.total"] == {"from": 587975, "to": 587000}
    assert entries[2].details["changes"]["totals.total"] == {"from": 587000, "to": 586000}


def test_changing_a_value_back_is_logged_as_a_change(database, doc):
    save(doc, edited(totals_total=587000))
    r = save(doc, ORIGINAL)
    assert r.json()["changes"] == [{"field": "totals.total", "from": 587000, "to": 587975}]
    assert [e.action for e in audit(database, doc)] == ["extracted", "edited", "edited"]


def test_clearing_a_field_is_logged(database, doc):
    r = save(doc, edited(date=None))
    assert r.json()["changes"] == [{"field": "date", "from": "2026-10-12", "to": None}]
    assert stored(database, doc).current_data["date"] is None


def test_an_added_and_a_removed_line_are_logged(database, doc):
    toll = {"description": "Toll", "quantity": 1, "unit_price": 100, "amount": 100}
    longer = edited()
    longer["line_items"].append(toll)
    added = save(doc, longer)
    assert added.json()["changes"] == [{"field": "line_items[3]", "from": None, "to": toll}]

    removed = save(doc, ORIGINAL)
    assert removed.json()["changes"] == [{"field": "line_items[3]", "from": toll, "to": None}]
    assert [e.action for e in audit(database, doc)] == ["extracted", "edited", "edited"]


def test_the_whole_invoice_can_be_cleared_and_the_original_is_still_there(database, doc):
    r = save(doc, {})
    changed_fields = [c["field"] for c in r.json()["changes"]]
    assert "supplier" in changed_fields and "totals.total" in changed_fields
    with database() as session:
        assert session.scalars(select(Extraction)).one().data == ORIGINAL


def test_the_search_keys_are_updated_with_the_edit(database, doc):
    before = stored(database, doc)
    assert (before.supplier_key, before.invoice_number_key) == ("kijani fuel distributors", "kfd20260412")
    save(doc, edited(supplier="Twiga Auto Spares", invoice_number="TAS/31002"))
    after = stored(database, doc)
    assert (after.supplier_key, after.invoice_number_key) == ("twiga auto spares", "tas31002")


def test_a_cleared_number_leaves_no_key(database, doc):
    save(doc, edited(invoice_number=""))
    assert stored(database, doc).invoice_number_key is None


def test_editing_the_number_clears_a_duplicate_flag_in_the_answer(monkeypatch, doc):
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: DIGITAL)
    rescan = (SAMPLES / "invoice_digital_rescan.pdf").read_bytes()
    second = client.post("/extract", files={"file": ("rescan.pdf", rescan, "application/pdf")}).json()
    assert "duplicate_invoice" in [f["code"] for f in second["flags"]]

    r = save(second["document_id"], edited(invoice_number="KFD-2026-9999"))
    assert "duplicate_invoice" not in [f["code"] for f in r.json()["flags"]]

    r = save(second["document_id"], ORIGINAL)
    assert "duplicate_invoice" in [f["code"] for f in r.json()["flags"]]


def test_a_document_does_not_flag_itself_when_saved(doc):
    assert "duplicate_invoice" not in [f["code"] for f in save(doc, edited(totals_total=1)).json()["flags"]]



@pytest.mark.parametrize("header, expected", [(None, "reviewer"), ("", "reviewer"), ("  Amina  ", "Amina")])
def test_the_reviewer_name_is_recorded(database, doc, header, expected):
    save(doc, edited(totals_total=1), **({"X-Reviewer": header} if header is not None else {}))
    assert audit(database, doc)[1].actor == expected


def test_an_unknown_document_is_a_404(database):
    r = save(str(uuid.uuid4()), ORIGINAL)
    assert r.status_code == 404
    assert r.json() == {"detail": "That document was not found."}


def test_an_id_that_is_not_a_uuid_is_refused():
    assert client.put("/documents/not-an-id", json={}).status_code == 422


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_a_decided_invoice_cannot_be_edited(database, doc, status):
    with database() as session:
        session.get(Document, uuid.UUID(doc)).status = status
        session.commit()
    r = save(doc, edited(totals_total=1))
    assert r.status_code == 409
    assert r.json() == {"detail": f"This invoice was already {status} and can no longer be edited."}
    assert stored(database, doc).current_data["totals"]["total"] == 587975
    assert [e.action for e in audit(database, doc)] == ["extracted"]


def test_a_body_that_is_not_an_invoice_object_is_refused(doc):
    assert client.put(f"/documents/{doc}", content="not json", headers={"content-type": "application/json"}).status_code == 422
    assert client.put(f"/documents/{doc}", json=[1, 2]).status_code == 422


def test_saving_needs_the_access_key(doc):
    anonymous = TestClient(main.app)
    r = anonymous.put(f"/documents/{doc}", json=ORIGINAL)
    assert r.status_code == 401
    r = anonymous.put(f"/documents/{doc}", json=ORIGINAL, headers={"X-Access-Key": "wrong"})
    assert r.status_code == 401


def test_the_browser_may_send_a_put_from_the_page(doc):
    r = TestClient(main.app).options(
        f"/documents/{doc}",
        headers={
            "Origin": main.settings.frontend_origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "x-access-key,content-type,x-reviewer",
        },
    )
    assert r.status_code == 200
    assert "PUT" in r.headers["access-control-allow-methods"]


def test_when_the_database_is_down_the_answer_is_a_clear_503(doc, monkeypatch):
    def broken(session, document_id, for_update=False):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(main, "get_document", broken)
    r = save(doc, ORIGINAL)
    assert r.status_code == 503
    assert "database is not available" in r.json()["detail"]


def test_a_failed_save_leaves_no_half_written_entry(database, doc, monkeypatch):
    def boom(session, document, invoice, actor):
        document.current_data = {"broken": True}
        raise OperationalError("UPDATE", {}, Exception("server closed the connection"))

    monkeypatch.setattr(main, "apply_edit", boom)
    assert save(doc, edited(totals_total=1)).status_code == 503
    assert stored(database, doc).current_data == ORIGINAL
    assert [e.action for e in audit(database, doc)] == ["extracted"]