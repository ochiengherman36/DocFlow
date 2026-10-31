import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

import main
from documents import file_hash, invoice_number_key, supplier_key_of
from extractor import ExtractionError
from models import AuditEntry, Document, Extraction
from pdf_reader import PdfReadError
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


class FakeExtractor:
    """Stands in for Claude and counts how many times it is asked."""

    def __init__(self, invoice: InvoiceData):
        self.invoice = invoice
        self.calls = 0

    def __call__(self, data, settings=None, client=None):
        self.calls += 1
        return self.invoice


@pytest.fixture
def extractor(monkeypatch):
    fake = FakeExtractor(invoice_of("invoice_digital.pdf"))
    monkeypatch.setattr(main, "extract_invoice", fake)
    return fake


def pdf(name="invoice_digital.pdf") -> bytes:
    return (SAMPLES / name).read_bytes()


def upload(data: bytes, filename="invoice.pdf", **headers):
    return client.post("/extract", files={"file": (filename, data, "application/pdf")}, headers=headers)


def count(database, model) -> int:
    with database() as session:
        return session.scalar(select(func.count()).select_from(model))


def codes(response) -> list[str]:
    return [f["code"] for f in response.json()["flags"]]


def test_a_new_file_is_extracted_and_saved(database, extractor):
    data = pdf()
    r = upload(data, "kijani.pdf")

    assert r.status_code == 200
    assert extractor.calls == 1
    body = r.json()
    assert body["status"] == "pending"
    assert body["filename"] == "kijani.pdf"
    assert body["invoice"]["supplier"] == "Kijani Fuel Distributors Ltd"
    assert "duplicate_file" not in codes(r)

    with database() as session:
        document = session.scalars(select(Document)).one()
        assert str(document.id) == body["document_id"]
        assert document.filename == "kijani.pdf"
        assert document.file_hash == hashlib.sha256(data).hexdigest()
        assert document.file_size == len(data)
        assert document.status == "pending"
        assert document.current_data["invoice_number"] == "KFD-2026-0412"
        assert document.supplier_key == "kijani fuel distributors"
        assert document.invoice_number_key == "kfd20260412"


def test_the_document_id_is_a_uuid(extractor):
    assert isinstance(uuid.UUID(upload(pdf()).json()["document_id"]), uuid.UUID)


def test_the_original_extraction_is_stored_with_the_model_name(database, extractor):
    upload(pdf())
    with database() as session:
        extraction = session.scalars(select(Extraction)).one()
        document = session.scalars(select(Document)).one()
        assert extraction.document_id == document.id
        assert extraction.model == "test-model"
        assert extraction.data == document.current_data
        assert extraction.data["totals"]["total"] == 587975
        assert extraction.data["date"] == "2026-10-12"


def test_the_extraction_is_a_separate_copy_of_the_data(database, extractor):
    upload(pdf())
    with database() as session:
        document = session.scalars(select(Document)).one()
        edited = dict(document.current_data)
        edited["supplier"] = "Someone Else"
        document.current_data = edited
        session.commit()
    with database() as session:
        assert session.scalars(select(Extraction)).one().data["supplier"] == "Kijani Fuel Distributors Ltd"


def test_an_extracted_audit_entry_is_written(database, extractor):
    data = pdf()
    upload(data, "kijani.pdf")
    with database() as session:
        entry = session.scalars(select(AuditEntry)).one()
        extraction = session.scalars(select(Extraction)).one()
        assert entry.action == "extracted"
        assert entry.actor == "reviewer"
        assert entry.extraction_id == extraction.id
        assert entry.created_at is not None
        assert entry.details["filename"] == "kijani.pdf"
        assert entry.details["file_size"] == len(data)
        assert entry.details["file_hash"] == hashlib.sha256(data).hexdigest()
        assert entry.details["model"] == "test-model"


def test_validation_flags_still_come_back_with_a_new_file(extractor):
    extractor.invoice = invoice_of("invoice_wrong_total.pdf")
    r = upload(pdf("invoice_wrong_total.pdf"))
    assert codes(r) == ["total_mismatch"]


def test_two_different_files_make_two_documents(database, extractor):
    upload(pdf("invoice_digital.pdf"))
    extractor.invoice = invoice_of("invoice_wrong_total.pdf")
    upload(pdf("invoice_wrong_total.pdf"))
    assert extractor.calls == 2
    assert count(database, Document) == 2
    assert count(database, Extraction) == 2
    assert count(database, AuditEntry) == 2


def test_the_same_file_twice_gives_one_document_one_model_call_and_a_flag(database, extractor):
    data = pdf()
    first = upload(data, "first.pdf")
    second = upload(data, "second-copy.pdf")

    assert second.status_code == 200
    assert extractor.calls == 1
    assert count(database, Document) == 1
    assert count(database, Extraction) == 1
    assert second.json()["document_id"] == first.json()["document_id"]
    assert "duplicate_file" in codes(second)


def test_the_duplicate_flag_is_a_warning_on_the_file_with_a_clear_message(extractor):
    data = pdf()
    upload(data)
    flag = next(f for f in upload(data).json()["flags"] if f["code"] == "duplicate_file")
    assert flag["severity"] == "warning"
    assert flag["field"] == "file"
    assert "already uploaded" in flag["message"]
    assert "status: pending" in flag["message"]


def test_a_duplicate_is_logged_in_the_audit_trail(database, extractor):
    data = pdf()
    upload(data, "first.pdf", **{"X-Reviewer": "Herman"})
    upload(data, "second-copy.pdf", **{"X-Reviewer": "Amina"})

    with database() as session:
        entries = session.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
        assert [e.action for e in entries] == ["extracted", "duplicate_upload"]
        assert [e.actor for e in entries] == ["Herman", "Amina"]
        assert entries[1].details == {"filename": "second-copy.pdf", "original_filename": "first.pdf"}
        assert entries[1].extraction_id == entries[0].extraction_id


def test_each_repeat_adds_its_own_audit_entry(database, extractor):
    data = pdf()
    for _ in range(4):
        upload(data)
    assert count(database, Document) == 1
    assert count(database, AuditEntry) == 4
    assert extractor.calls == 1


def test_the_duplicate_answer_has_the_same_invoice_data(extractor):
    data = pdf()
    first = upload(data).json()
    second = upload(data).json()
    assert second["invoice"] == first["invoice"]
    assert second["status"] == "pending"


def test_a_duplicate_shows_the_reviewers_current_data_not_the_original(database, extractor):
    data = pdf()
    upload(data)
    with database() as session:
        document = session.scalars(select(Document)).one()
        edited = dict(document.current_data)
        edited["totals"] = {**edited["totals"], "total": 1.0}
        document.current_data = edited
        session.commit()

    second = upload(data).json()
    assert second["invoice"]["totals"]["total"] == 1.0
    with database() as session:
        assert session.scalars(select(Extraction)).one().data["totals"]["total"] == 587975


def test_a_duplicate_is_checked_again_against_the_current_rules(database, extractor):
    data = pdf("invoice_wrong_total.pdf")
    extractor.invoice = invoice_of("invoice_wrong_total.pdf")
    first = upload(data)
    second = upload(data)
    assert codes(first) == ["total_mismatch"]
    assert codes(second) == ["duplicate_file", "total_mismatch"]


def test_a_duplicate_reports_the_stored_status(database, extractor):
    data = pdf()
    upload(data)
    with database() as session:
        session.scalars(select(Document)).one().status = "approved"
        session.commit()
    r = upload(data)
    assert r.json()["status"] == "approved"
    flag = next(f for f in r.json()["flags"] if f["code"] == "duplicate_file")
    assert "status: approved" in flag["message"]


def test_a_file_that_was_rejected_is_still_found(database, extractor):
    data = pdf()
    upload(data)
    with database() as session:
        session.scalars(select(Document)).one().status = "rejected"
        session.commit()
    r = upload(data)
    assert extractor.calls == 1
    assert r.json()["status"] == "rejected"


def test_a_file_with_one_byte_changed_is_a_new_document(database, extractor):
    data = pdf()
    upload(data)
    upload(data + b"\n")
    assert extractor.calls == 2
    assert count(database, Document) == 2


def test_the_reviewer_name_is_recorded(database, extractor):
    upload(pdf(), **{"X-Reviewer": "  Herman Otieno  "})
    with database() as session:
        assert session.scalars(select(AuditEntry)).one().actor == "Herman Otieno"


@pytest.mark.parametrize("header", [None, "", "   "])
def test_a_missing_or_blank_name_becomes_reviewer(database, extractor, header):
    upload(pdf(), **({"X-Reviewer": header} if header is not None else {}))
    with database() as session:
        assert session.scalars(select(AuditEntry)).one().actor == "reviewer"


def test_a_very_long_name_is_cut_to_fit_the_column(database, extractor):
    upload(pdf(), **{"X-Reviewer": "H" * 500})
    with database() as session:
        assert len(session.scalars(select(AuditEntry)).one().actor) == 100


def test_a_failed_extraction_saves_nothing(database, monkeypatch):
    def broken(data, settings=None, client=None):
        raise ExtractionError("Rate limited by the Anthropic API. Try again in a moment.")

    monkeypatch.setattr(main, "extract_invoice", broken)
    r = upload(pdf())
    assert r.status_code == 502
    assert (count(database, Document), count(database, Extraction), count(database, AuditEntry)) == (0, 0, 0)


def test_an_unreadable_pdf_saves_nothing(database, monkeypatch):
    def broken(data, settings=None, client=None):
        raise PdfReadError("Could not read PDF: broken")

    monkeypatch.setattr(main, "extract_invoice", broken)
    assert upload(pdf()).status_code == 400
    assert count(database, Document) == 0


def test_a_retry_after_a_failure_works(database, monkeypatch):
    state = {"fail": True}
    good = invoice_of("invoice_digital.pdf")

    def flaky(data, settings=None, client=None):
        if state["fail"]:
            raise ExtractionError("temporary problem")
        return good

    monkeypatch.setattr(main, "extract_invoice", flaky)
    assert upload(pdf()).status_code == 502
    state["fail"] = False
    assert upload(pdf()).status_code == 200
    assert count(database, Document) == 1


@pytest.mark.parametrize(
    "files, status",
    [
        ({"file": ("empty.pdf", b"", "application/pdf")}, 400),
        ({"file": ("notes.pdf", b"just some text", "application/pdf")}, 400),
        ({"file": ("big.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024), "application/pdf")}, 413),
        (None, 400),
    ],
)
def test_bad_uploads_save_nothing_and_never_reach_the_model(database, extractor, files, status):
    r = client.post("/extract", files=files) if files else client.post("/extract")
    assert r.status_code == status
    assert extractor.calls == 0
    assert count(database, Document) == 0


def test_when_the_database_is_down_the_model_is_not_called(extractor, monkeypatch):
    def broken(session, digest):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(main, "find_by_hash", broken)
    r = upload(pdf())
    assert r.status_code == 503
    assert r.json() == {"detail": "The database is not available. Please try again in a moment."}
    assert extractor.calls == 0


def test_when_the_save_fails_the_answer_is_a_clear_503(extractor, monkeypatch):
    def broken(session, **kwargs):
        raise OperationalError("INSERT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(main, "save_new_document", broken)
    r = upload(pdf())
    assert r.status_code == 503
    assert "database is not available" in r.json()["detail"]


def test_two_requests_for_the_same_new_file_at_once_give_one_document(database, monkeypatch):
    data = pdf()
    invoice = invoice_of("invoice_digital.pdf")
    calls = []

    def racing(data_, settings=None, client=None):
        calls.append(1)
        if len(calls) == 1:
            with database() as other:
                from documents import save_new_document

                save_new_document(other, filename="other.pdf", digest=file_hash(data), size=len(data),
                                  invoice=invoice, model="test-model", actor="someone else")
                other.commit()
        return invoice

    monkeypatch.setattr(main, "extract_invoice", racing)
    r = upload(data, "mine.pdf")

    assert r.status_code == 200
    assert "duplicate_file" in codes(r)
    assert count(database, Document) == 1
    assert count(database, Extraction) == 1
    with database() as session:
        assert [e.action for e in session.scalars(select(AuditEntry).order_by(AuditEntry.id))] == [
            "extracted",
            "duplicate_upload",
        ]


def test_file_hash_is_sha256_and_depends_on_every_byte():
    assert file_hash(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert file_hash(b"abc") != file_hash(b"abd")
    assert len(file_hash(b"")) == 64


@pytest.mark.parametrize(
    "number, key",
    [
        ("KFD-2026-0412", "kfd20260412"),
        ("MOS/INV/7781", "mosinv7781"),
        ("mos-inv-7781", "mosinv7781"),
        ("  INV 007  ", "inv007"),
        ("A.B.C", "abc"),
    ],
)
def test_invoice_numbers_are_compared_without_case_or_punctuation(number, key):
    assert invoice_number_key(number) == key


def test_the_same_number_written_two_ways_gives_one_key():
    assert invoice_number_key("MOS/INV/7781") == invoice_number_key("mos-inv-7781")


@pytest.mark.parametrize("blank", [None, "", "   ", "---", "///"])
def test_a_missing_or_empty_number_has_no_key(blank):
    assert invoice_number_key(blank) is None


def test_a_very_long_number_is_cut_to_the_column_size():
    assert len(invoice_number_key("A" * 300)) == 100


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_missing_supplier_has_no_key(blank):
    assert supplier_key_of(blank) is None


def test_supplier_keys_ignore_ltd_and_case():
    assert supplier_key_of("Kijani Fuel Distributors Ltd") == supplier_key_of("KIJANI FUEL DISTRIBUTORS LIMITED")