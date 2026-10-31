import datetime
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

import main
from models import Document
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


def upload(monkeypatch, name: str, **headers) -> str:
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: invoice_of(name))
    pdf = (SAMPLES / name).read_bytes()
    r = client.post("/extract", files={"file": (name, pdf, "application/pdf")}, headers=headers)
    assert r.status_code == 200
    return r.json()["document_id"]


DIGITAL = "invoice_digital.pdf"


@pytest.fixture
def doc(monkeypatch):
    return upload(monkeypatch, DIGITAL, **{"X-Reviewer": "Herman"})


def trail(document_id) -> dict:
    r = client.get(f"/documents/{document_id}/audit")
    assert r.status_code == 200
    return r.json()


def set_created(database, document_id: str, when: datetime.datetime) -> None:
    with database() as session:
        session.execute(update(Document).where(Document.id == uuid.UUID(document_id)).values(created_at=when))
        session.commit()

def test_the_trail_lists_newest_first_in_the_order_things_happened(doc):
    data = invoice_of(DIGITAL).model_dump(mode="json")
    data["currency"] = "USD"
    client.put(f"/documents/{doc}", json=data, headers={"X-Reviewer": "Wanjiru"})
    client.post(f"/documents/{doc}/approve", headers={"X-Reviewer": "Brian"})

    entries = trail(doc)["entries"]

    assert [(e["action"], e["actor"]) for e in entries] == [
        ("approved", "Brian"),
        ("edited", "Wanjiru"),
        ("extracted", "Herman"),
    ]


def test_every_entry_has_a_time_and_ids_fall_as_you_go_down(doc):
    client.post(f"/documents/{doc}/reject", json={"reason": "No"})
    entries = trail(doc)["entries"]
    assert all(e["at"] for e in entries)
    ids = [e["id"] for e in entries]
    assert ids == sorted(ids, reverse=True)


def test_the_order_holds_when_entries_share_a_timestamp(doc, monkeypatch):
    # Entries written within the same second still come back in the order they happened.
    upload(monkeypatch, DIGITAL)  # the same file again: logs a duplicate_upload
    client.post(f"/documents/{doc}/approve")
    actions = [e["action"] for e in trail(doc)["entries"]]
    assert actions == ["approved", "duplicate_upload", "extracted"]


def test_entries_carry_their_details(doc):
    data = invoice_of(DIGITAL).model_dump(mode="json")
    data["totals"]["total"] = 587000
    client.put(f"/documents/{doc}", json=data)
    entries = trail(doc)["entries"]
    assert entries[0]["details"] == {"changes": {"totals.total": {"from": 587975, "to": 587000}}}
    assert entries[1]["details"]["filename"] == "invoice_digital.pdf"


def test_a_rejection_shows_its_reason(doc):
    client.post(f"/documents/{doc}/reject", json={"reason": "Not ours"})
    assert trail(doc)["entries"][0]["details"] == {"reason": "Not ours"}


def test_the_trail_names_the_document_and_its_status(doc):
    client.post(f"/documents/{doc}/reject", json={"reason": "No"})
    body = trail(doc)
    assert body["document_id"] == doc
    assert body["status"] == "rejected"


def test_a_trail_holds_only_its_own_document(doc, monkeypatch):
    other = upload(monkeypatch, "invoice_scanned.pdf")
    client.post(f"/documents/{other}/approve")
    assert [e["action"] for e in trail(doc)["entries"]] == ["extracted"]
    assert [e["action"] for e in trail(other)["entries"]] == ["approved", "extracted"]


def test_an_unknown_document_has_no_trail():
    assert client.get(f"/documents/{uuid.uuid4()}/audit").status_code == 404


def test_a_bad_id_is_a_422():
    assert client.get("/documents/not-a-uuid/audit").status_code == 422


def test_the_trail_needs_the_access_key(doc):
    assert TestClient(main.app).get(f"/documents/{doc}/audit").status_code == 401


def test_reading_the_trail_changes_nothing(doc):
    before = trail(doc)
    after = trail(doc)
    assert before == after

def test_an_empty_system_lists_nothing():
    assert client.get("/documents").json() == {"documents": [], "total": 0}


def test_each_row_has_the_summary_a_list_needs(doc):
    body = client.get("/documents").json()
    assert body["total"] == 1
    row = body["documents"][0]
    assert row["document_id"] == doc
    assert row["filename"] == "invoice_digital.pdf"
    assert row["status"] == "pending"
    assert row["supplier"] == "Kijani Fuel Distributors Ltd"
    assert row["invoice_number"] == "KFD-2026-0412"
    assert row["date"] == "2026-10-12"
    assert row["currency"] == "KES"
    assert row["total"] == 587975
    assert row["created_at"]
    assert row["decided_by"] is None and row["decided_at"] is None and row["reject_reason"] is None


def test_the_list_shows_the_edited_version(doc):
    data = invoice_of(DIGITAL).model_dump(mode="json")
    data["supplier"] = "Someone Else Ltd"
    data["totals"]["total"] = 1
    client.put(f"/documents/{doc}", json=data)
    row = client.get("/documents").json()["documents"][0]
    assert (row["supplier"], row["total"]) == ("Someone Else Ltd", 1)


def test_a_decision_shows_who_when_and_why(doc, monkeypatch):
    other = upload(monkeypatch, "invoice_scanned.pdf")
    client.post(f"/documents/{doc}/approve", headers={"X-Reviewer": "Brian"})
    client.post(f"/documents/{other}/reject", json={"reason": "Wrong company"}, headers={"X-Reviewer": "Wanjiru"})

    rows = {r["document_id"]: r for r in client.get("/documents").json()["documents"]}

    assert (rows[doc]["status"], rows[doc]["decided_by"]) == ("approved", "Brian")
    assert rows[doc]["decided_at"]
    assert (rows[other]["status"], rows[other]["decided_by"]) == ("rejected", "Wanjiru")
    assert rows[other]["reject_reason"] == "Wrong company"


def test_the_list_is_newest_first(database, monkeypatch):
    names = ["invoice_digital.pdf", "invoice_scanned.pdf", "invoice_poor_quality.pdf"]
    ids = [upload(monkeypatch, name) for name in names]
    day = datetime.datetime(2026, 10, 1, 9, 0)
    for offset, document_id in zip([2, 0, 1], ids):  # the scanned one is the oldest
        set_created(database, document_id, day + datetime.timedelta(days=offset))

    listed = [r["document_id"] for r in client.get("/documents").json()["documents"]]

    assert listed == [ids[0], ids[2], ids[1]]


def test_missing_values_come_back_as_null(monkeypatch):
    document_id = upload(monkeypatch, "invoice_missing_date.pdf")
    row = client.get("/documents").json()["documents"][0]
    assert row["document_id"] == document_id
    assert row["date"] is None


def test_the_list_can_be_filtered_by_status(doc, monkeypatch):
    other = upload(monkeypatch, "invoice_scanned.pdf")
    client.post(f"/documents/{other}/approve")

    def ids(status):
        body = client.get("/documents", params={"status": status}).json()
        return [r["document_id"] for r in body["documents"]], body["total"]

    assert ids("pending") == ([doc], 1)
    assert ids("approved") == ([other], 1)
    assert ids("rejected") == ([], 0)


def test_an_unknown_status_is_a_422():
    assert client.get("/documents", params={"status": "archived"}).status_code == 422


def test_paging_and_the_total(database, monkeypatch):
    names = ["invoice_digital.pdf", "invoice_scanned.pdf", "invoice_poor_quality.pdf"]
    ids = [upload(monkeypatch, name) for name in names]
    day = datetime.datetime(2026, 10, 1, 9, 0)
    for offset, document_id in enumerate(ids):  # ids[2] is the newest
        set_created(database, document_id, day + datetime.timedelta(days=offset))

    first = client.get("/documents", params={"limit": 2}).json()
    second = client.get("/documents", params={"limit": 2, "offset": 2}).json()

    assert [r["document_id"] for r in first["documents"]] == [ids[2], ids[1]]
    assert [r["document_id"] for r in second["documents"]] == [ids[0]]
    assert first["total"] == second["total"] == 3


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"limit": "many"}])
def test_bad_paging_is_a_422(params):
    assert client.get("/documents", params=params).status_code == 422


def test_the_list_needs_the_access_key():
    assert TestClient(main.app).get("/documents").status_code == 401


def test_the_list_does_not_break_saving_a_document(doc):
    # GET /documents and PUT /documents/{id} share a path prefix.
    data = invoice_of(DIGITAL).model_dump(mode="json")
    assert client.put(f"/documents/{doc}", json=data).status_code == 200


def test_cors_allows_the_screen_to_read_both(doc):
    for path in ("/documents", f"/documents/{doc}/audit"):
        r = client.get(path, headers={"Origin": "http://localhost:3000"})
        assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


@pytest.mark.parametrize("path", ["/documents", "/documents/{id}/audit"])
def test_a_database_failure_is_a_503(doc, monkeypatch, path):
    def broken(*args, **kwargs):
        raise OperationalError("select", {}, Exception("connection lost"))

    monkeypatch.setattr(main, "list_documents", broken)
    monkeypatch.setattr(main, "get_document", broken)
    r = client.get(path.format(id=doc))
    assert r.status_code == 503
    assert "database" in r.json()["detail"].lower()