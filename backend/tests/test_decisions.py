import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

import main
from models import AuditEntry, Document, Supplier
from schemas import InvoiceData
from supplier_store import list_supplier_names

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


def upload(monkeypatch, name: str) -> str:
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: invoice_of(name))
    pdf = (SAMPLES / name).read_bytes()
    r = client.post("/extract", files={"file": (name, pdf, "application/pdf")})
    assert r.status_code == 200
    return r.json()["document_id"]


DIGITAL = "invoice_digital.pdf"
RESCAN = "invoice_digital_rescan.pdf"  # the same invoice as DIGITAL, a different file


@pytest.fixture
def doc(monkeypatch):
    return upload(monkeypatch, DIGITAL)


def edit(document_id, **changes):
    data = invoice_of(DIGITAL).model_dump(mode="json")
    data.update(changes)
    r = client.put(f"/documents/{document_id}", json=data)
    assert r.status_code == 200
    return r


def approve(document_id, **headers):
    return client.post(f"/documents/{document_id}/approve", headers=headers)


def reject(document_id, reason="Not ours", **headers):
    return client.post(f"/documents/{document_id}/reject", json={"reason": reason}, headers=headers)


def audit(database, document_id) -> list[AuditEntry]:
    with database() as session:
        return list(
            session.scalars(
                select(AuditEntry).where(AuditEntry.document_id == uuid.UUID(document_id)).order_by(AuditEntry.id)
            )
        )


def stored(database, document_id) -> Document:
    with database() as session:
        return session.get(Document, uuid.UUID(document_id))


def supplier_count(database) -> int:
    with database() as session:
        return session.scalar(select(func.count()).select_from(Supplier))


def upload_second_for_supplier(monkeypatch, supplier: str, number: str) -> str:
    # Any other file will do; its content is replaced by the save below.
    document_id = upload(monkeypatch, "invoice_poor_quality.pdf")
    data = invoice_of(DIGITAL).model_dump(mode="json")
    data.update(supplier=supplier, invoice_number=number)
    assert client.put(f"/documents/{document_id}", json=data).status_code == 200
    return document_id


def test_a_clean_invoice_is_approved_with_who_and_when(database, doc):
    r = approve(doc, **{"X-Reviewer": "Herman"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["decided_by"] == "Herman"
    assert body["decided_at"]

    row = stored(database, doc)
    assert row.status == "approved"
    assert row.decided_by == "Herman"
    assert row.decided_at is not None


def test_the_reviewer_defaults_when_no_name_is_sent(database, doc):
    assert approve(doc).json()["decided_by"] == "reviewer"


def test_approving_logs_one_entry_with_who(database, doc):
    approve(doc, **{"X-Reviewer": "Herman"})
    entries = audit(database, doc)
    assert [e.action for e in entries] == ["extracted", "approved"]
    assert entries[1].actor == "Herman"
    assert entries[1].extraction_id == entries[0].extraction_id
    assert entries[1].details == {"supplier_added": False, "warnings": []}


def test_the_approved_data_is_the_saved_data(database, doc):
    edit(doc, currency="USD")
    approve(doc)
    assert stored(database, doc).current_data["currency"] == "USD"


def test_errors_block_approval_and_the_response_carries_the_flags(database, doc):
    edited = edit(doc, totals={"subtotal": 506875, "tax": 81100, "total": 587000})
    assert [f["code"] for f in edited.json()["flags"]] == ["total_mismatch"]

    r = approve(doc)

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "errors" in detail["message"]
    assert [f["code"] for f in detail["flags"]] == ["total_mismatch"]
    assert detail["flags"][0]["severity"] == "error"
    assert detail["flags"][0]["field"] == "totals.total"

    row = stored(database, doc)
    assert row.status == "pending"
    assert row.decided_by is None and row.decided_at is None
    assert [e.action for e in audit(database, doc)] == ["extracted", "edited"]


def test_the_check_runs_on_the_server_even_if_nobody_validated(database, monkeypatch):
    # An invoice with a missing date is still refused: approve does not trust the screen.
    document_id = upload(monkeypatch, "invoice_missing_date.pdf")
    r = approve(document_id)
    assert r.status_code == 409
    assert "missing_field" in [f["code"] for f in r.json()["detail"]["flags"]]


def test_warnings_do_not_block_approval(database, doc):
    edit(doc, supplier="Brand New Traders Ltd")
    r = approve(doc)

    assert r.status_code == 200
    assert [f["code"] for f in r.json()["flags"]] == ["supplier_unknown"]
    assert audit(database, doc)[-1].details["warnings"] == ["supplier_unknown"]


def test_a_new_supplier_is_added_to_the_list(database, doc):
    before = supplier_count(database)
    edit(doc, supplier="Brand New Traders Ltd")

    r = approve(doc)

    assert r.json()["supplier_added"] is True
    assert supplier_count(database) == before + 1
    with database() as session:
        assert "Brand New Traders Ltd" in list_supplier_names(session)
    assert stored(database, doc).supplier_id is not None
    assert audit(database, doc)[-1].details["supplier_added"] is True


def test_once_added_the_supplier_is_known_to_later_invoices(doc):
    edit(doc, supplier="Brand New Traders Ltd")
    approve(doc)

    other = invoice_of(DIGITAL).model_dump(mode="json")
    other["supplier"] = "Brand New Traders Ltd"
    flags = client.post("/validate", json=other).json()["flags"]
    assert "supplier_unknown" not in [f["code"] for f in flags]


def test_a_known_supplier_is_not_added_again(database, doc):
    before = supplier_count(database)
    r = approve(doc)
    assert r.json()["supplier_added"] is False
    assert supplier_count(database) == before
    assert stored(database, doc).supplier_id is not None


def test_the_same_supplier_spelled_differently_is_not_added_twice(database, doc, monkeypatch):
    edit(doc, supplier="Brand New Traders Ltd")
    approve(doc)
    count = supplier_count(database)

    second = upload_second_for_supplier(monkeypatch, "BRAND NEW TRADERS LTD", "BNT-1")
    assert approve(second).json()["supplier_added"] is False
    assert supplier_count(database) == count


def test_a_duplicate_of_an_approved_invoice_is_refused(database, doc, monkeypatch):
    assert approve(doc).status_code == 200
    again = upload(monkeypatch, RESCAN)

    r = approve(again)

    assert r.status_code == 409
    assert "duplicate_invoice" in [f["code"] for f in r.json()["detail"]["flags"]]
    assert stored(database, again).status == "pending"
    assert [e.action for e in audit(database, again)] == ["extracted"]


def test_a_pending_duplicate_is_only_a_warning(database, doc, monkeypatch):
    again = upload(monkeypatch, RESCAN)
    r = approve(again)
    assert r.status_code == 200
    assert "duplicate_invoice" in [f["code"] for f in r.json()["flags"]]


def test_after_one_copy_is_approved_the_other_is_blocked(doc, monkeypatch):
    again = upload(monkeypatch, RESCAN)
    assert approve(again).status_code == 200
    assert approve(doc).status_code == 409


def test_a_rejected_copy_does_not_block_approval(doc, monkeypatch):
    again = upload(monkeypatch, RESCAN)
    assert reject(again).status_code == 200
    r = approve(doc)
    assert r.status_code == 200
    assert "duplicate_invoice" not in [f["code"] for f in r.json()["flags"]]


def test_the_unique_index_stops_a_duplicate_the_check_missed(database, doc, monkeypatch):
    again = upload(monkeypatch, RESCAN)
    assert approve(again).status_code == 200
    before = supplier_count(database)

    # Pretend the check ran just before the other copy was approved.
    monkeypatch.setattr(main, "check_invoice", lambda invoice, session, exclude=None: [])
    r = approve(doc)

    assert r.status_code == 409
    assert "a moment ago" in r.json()["detail"]["message"]
    row = stored(database, doc)
    assert row.status == "pending"
    assert row.decided_by is None
    assert [e.action for e in audit(database, doc)] == ["extracted"]
    assert supplier_count(database) == before

    # The refusal leaves the document usable: once the check is real it is simply blocked.
    monkeypatch.undo()
    assert approve(doc).status_code == 409


def test_a_rejection_stores_the_reason_who_and_when(database, doc):
    r = reject(doc, reason="Not our supplier", **{"X-Reviewer": "Herman"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert body["decided_by"] == "Herman"
    assert body["decided_at"]
    assert body["reject_reason"] == "Not our supplier"

    row = stored(database, doc)
    assert (row.status, row.decided_by, row.reject_reason) == ("rejected", "Herman", "Not our supplier")
    assert row.decided_at is not None


def test_rejecting_logs_the_reason(database, doc):
    reject(doc, reason="Not our supplier", **{"X-Reviewer": "Herman"})
    entries = audit(database, doc)
    assert [e.action for e in entries] == ["extracted", "rejected"]
    assert entries[1].actor == "Herman"
    assert entries[1].details == {"reason": "Not our supplier"}


def test_the_reason_is_trimmed(database, doc):
    assert reject(doc, reason="   Wrong company \n").json()["reject_reason"] == "Wrong company"


@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_a_reason_is_required(database, doc, reason):
    r = reject(doc, reason=reason)
    assert r.status_code == 422
    assert stored(database, doc).status == "pending"
    assert [e.action for e in audit(database, doc)] == ["extracted"]


def test_a_missing_reason_is_refused(database, doc):
    assert client.post(f"/documents/{doc}/reject", json={}).status_code == 422
    assert client.post(f"/documents/{doc}/reject").status_code == 422
    assert stored(database, doc).status == "pending"


def test_a_very_long_reason_is_refused(doc):
    assert reject(doc, reason="x" * 1001).status_code == 422
    assert reject(doc, reason="x" * 1000).status_code == 200


def test_rejecting_adds_no_supplier(database, doc):
    edit(doc, supplier="Brand New Traders Ltd")
    before = supplier_count(database)
    reject(doc)
    assert supplier_count(database) == before


def test_a_rejected_document_cannot_be_approved(database, doc):
    reject(doc)
    r = approve(doc)
    assert r.status_code == 409
    assert r.json()["detail"] == "This invoice was already rejected."
    assert stored(database, doc).status == "rejected"
    assert [e.action for e in audit(database, doc)] == ["extracted", "rejected"]


def test_an_approved_document_cannot_be_rejected(database, doc):
    approve(doc)
    r = reject(doc)
    assert r.status_code == 409
    assert r.json()["detail"] == "This invoice was already approved."
    row = stored(database, doc)
    assert row.status == "approved"
    assert row.reject_reason is None
    assert [e.action for e in audit(database, doc)] == ["extracted", "approved"]


def test_approving_twice_logs_once(database, doc):
    first = approve(doc, **{"X-Reviewer": "Herman"})
    second = approve(doc, **{"X-Reviewer": "Someone Else"})
    assert (first.status_code, second.status_code) == (200, 409)
    assert stored(database, doc).decided_by == "Herman"
    assert [e.action for e in audit(database, doc)] == ["extracted", "approved"]


def test_rejecting_twice_keeps_the_first_reason(database, doc):
    reject(doc, reason="First")
    assert reject(doc, reason="Second").status_code == 409
    assert stored(database, doc).reject_reason == "First"


@pytest.mark.parametrize("decide", [approve, reject])
def test_a_decided_document_can_no_longer_be_edited(doc, decide):
    decide(doc)
    r = client.put(f"/documents/{doc}", json=invoice_of(DIGITAL).model_dump(mode="json"))
    assert r.status_code == 409


@pytest.mark.parametrize("decide", [approve, reject])
def test_an_unknown_document_is_a_404(decide):
    assert decide(str(uuid.uuid4())).status_code == 404


@pytest.mark.parametrize("path", ["approve", "reject"])
def test_a_bad_id_is_a_422(path):
    assert client.post(f"/documents/not-a-uuid/{path}", json={"reason": "x"}).status_code == 422


@pytest.mark.parametrize("path", ["approve", "reject"])
def test_deciding_needs_the_access_key(doc, path):
    r = TestClient(main.app).post(f"/documents/{doc}/{path}", json={"reason": "x"})
    assert r.status_code == 401


@pytest.mark.parametrize("decide", [approve, reject])
def test_a_database_failure_is_a_503(doc, monkeypatch, decide):
    def broken(*args, **kwargs):
        raise OperationalError("select", {}, Exception("connection lost"))

    monkeypatch.setattr(main, "get_document", broken)
    r = decide(doc)
    assert r.status_code == 503
    assert "database" in r.json()["detail"].lower()