import datetime
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import main
from documents import find_duplicate_invoices, save_new_document
from models import Document
from schemas import FlagCode, InvoiceData, Severity
from validation import DuplicateMatch, validate

HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)

SAMPLES = Path(__file__).parent.parent / "samples"
EXPECTED = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
TODAY = datetime.date(2026, 10, 30)
WHEN = datetime.datetime(2026, 10, 25, 9, 30)


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


def pdf(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


def upload(data: bytes, filename: str = "invoice.pdf", **headers):
    return client.post("/extract", files={"file": (filename, data, "application/pdf")}, headers=headers)


def dup_flags(response_or_flags) -> list[dict]:
    flags = response_or_flags.json()["flags"] if hasattr(response_or_flags, "json") else response_or_flags
    return [f for f in flags if f["code"] == "duplicate_invoice"]


@pytest.fixture
def digital_model(monkeypatch):
    """Claude is faked. Every file is read as the digital invoice, whatever its bytes."""
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: DIGITAL)


def add_document(session, invoice=DIGITAL, *, status="pending", filename=None, seed=None):
    seed = seed or uuid.uuid4().hex
    document = save_new_document(
        session,
        filename=filename or f"{seed[:6]}.pdf",
        digest=hashlib.sha256(seed.encode()).hexdigest(),
        size=1,
        invoice=invoice,
        model="m",
        actor="x",
    )
    document.status = status
    session.commit()
    return document


def test_no_matches_means_no_flag():
    assert validate(DIGITAL, today=TODAY, duplicates=[]) == []


def test_a_waiting_copy_is_a_warning_on_the_invoice_number():
    flags = validate(DIGITAL, today=TODAY, duplicates=[DuplicateMatch("pending", "first.pdf", WHEN)])
    assert [(f.code, f.severity, f.field) for f in flags] == [
        (FlagCode.DUPLICATE_INVOICE, Severity.WARNING, "invoice_number")
    ]
    assert "KFD-2026-0412" in flags[0].message
    assert "Kijani Fuel Distributors Ltd" in flags[0].message
    assert "25 Oct 2026" in flags[0].message
    assert "awaiting review" in flags[0].message
    assert "first.pdf" in flags[0].message


def test_an_approved_copy_is_an_error():
    flags = validate(DIGITAL, today=TODAY, duplicates=[DuplicateMatch("approved", "first.pdf", WHEN)])
    assert [(f.code, f.severity) for f in flags] == [(FlagCode.DUPLICATE_INVOICE, Severity.ERROR)]
    assert "already approved on 25 Oct 2026" in flags[0].message


def test_when_both_exist_the_approved_one_decides():
    matches = [DuplicateMatch("pending", "waiting.pdf", WHEN), DuplicateMatch("approved", "done.pdf", WHEN)]
    flags = validate(DIGITAL, today=TODAY, duplicates=matches)
    assert len(flags) == 1
    assert flags[0].severity is Severity.ERROR
    assert "done.pdf" in flags[0].message
    assert "and 1 more like it" in flags[0].message


def test_several_waiting_copies_make_one_flag():
    matches = [DuplicateMatch("pending", f"{n}.pdf", WHEN) for n in range(3)]
    flags = validate(DIGITAL, today=TODAY, duplicates=matches)
    assert len(flags) == 1
    assert "and 2 more like it" in flags[0].message


def test_an_unknown_date_is_handled():
    flags = validate(DIGITAL, today=TODAY, duplicates=[DuplicateMatch("pending", "x.pdf", None)])
    assert "an earlier date" in flags[0].message


def test_without_an_invoice_number_there_is_nothing_to_flag():
    no_number = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), "invoice_number": None})
    flags = validate(no_number, today=TODAY, duplicates=[DuplicateMatch("pending", "x.pdf", WHEN)])
    assert FlagCode.DUPLICATE_INVOICE not in [f.code for f in flags]


def test_a_stored_invoice_with_the_same_supplier_and_number_is_found(database):
    with database() as session:
        add_document(session, filename="original.pdf")
        matches = find_duplicate_invoices(session, DIGITAL)
    assert [(m.status, m.filename) for m in matches] == [("pending", "original.pdf")]


@pytest.mark.parametrize(
    "changes",
    [
        {"invoice_number": "mos-inv-7781"},
        {"invoice_number": "KFD 2026 0413"},
        {"supplier": "Twiga Auto Spares"},
    ],
)
def test_a_different_number_or_supplier_is_not_a_match(database, changes):
    other = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), **changes})
    with database() as session:
        add_document(session)
        assert find_duplicate_invoices(session, other) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"invoice_number": "kfd-2026-0412"},
        {"invoice_number": "KFD/2026/0412"},
        {"invoice_number": " KFD 2026 0412 "},
        {"supplier": "KIJANI FUEL DISTRIBUTORS LIMITED"},
        {"supplier": "kijani fuel distributors"},
    ],
)
def test_case_punctuation_and_ltd_do_not_hide_a_match(database, changes):
    same = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), **changes})
    with database() as session:
        add_document(session)
        assert len(find_duplicate_invoices(session, same)) == 1


def test_a_rejected_invoice_does_not_count(database):
    with database() as session:
        add_document(session, status="rejected")
        assert find_duplicate_invoices(session, DIGITAL) == []


def test_a_document_never_matches_itself(database):
    with database() as session:
        document = add_document(session)
        assert len(find_duplicate_invoices(session, DIGITAL)) == 1
        assert find_duplicate_invoices(session, DIGITAL, exclude_id=document.id) == []


def test_excluding_one_document_still_finds_the_others(database):
    with database() as session:
        first = add_document(session, filename="a.pdf")
        add_document(session, filename="b.pdf")
        matches = find_duplicate_invoices(session, DIGITAL, exclude_id=first.id)
    assert [m.filename for m in matches] == ["b.pdf"]


def test_approved_matches_come_first(database):
    with database() as session:
        add_document(session, filename="waiting.pdf")
        add_document(session, filename="done.pdf", status="approved")
        matches = find_duplicate_invoices(session, DIGITAL)
    assert [(m.status, m.filename) for m in matches] == [("approved", "done.pdf"), ("pending", "waiting.pdf")]


@pytest.mark.parametrize("missing", [{"supplier": None}, {"invoice_number": None}, {"supplier": "", "invoice_number": ""}])
def test_an_invoice_without_a_supplier_or_number_cannot_match(database, missing):
    partial = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), **missing})
    with database() as session:
        add_document(session)
        assert find_duplicate_invoices(session, partial) == []


def test_the_rescan_sample_has_different_bytes_from_the_original():
    original, rescan = pdf("invoice_digital.pdf"), pdf("invoice_digital_rescan.pdf")
    assert hashlib.sha256(original).hexdigest() != hashlib.sha256(rescan).hexdigest()
    assert EXPECTED["invoice_digital_rescan.pdf"] == EXPECTED["invoice_digital.pdf"]


def test_the_rescan_matches_the_original(database, digital_model):
    first = upload(pdf("invoice_digital.pdf"), "original.pdf")
    second = upload(pdf("invoice_digital_rescan.pdf"), "rescan.pdf")

    assert dup_flags(first) == []
    flags = dup_flags(second)
    assert len(flags) == 1
    assert flags[0]["severity"] == "warning"
    assert flags[0]["field"] == "invoice_number"
    assert "original.pdf" in flags[0]["message"]
    assert second.json()["document_id"] != first.json()["document_id"]
    assert "duplicate_file" not in [f["code"] for f in second.json()["flags"]]
    with database() as session:
        assert len(session.scalars(select(Document)).all()) == 2


def test_the_rescan_of_an_approved_invoice_is_an_error(database, digital_model):
    first = upload(pdf("invoice_digital.pdf"))
    with database() as session:
        session.get(Document, uuid.UUID(first.json()["document_id"])).status = "approved"
        session.commit()
    flags = dup_flags(upload(pdf("invoice_digital_rescan.pdf")))
    assert [f["severity"] for f in flags] == ["error"]
    assert "already approved" in flags[0]["message"]


def test_the_rescan_of_a_rejected_invoice_is_fine(database, digital_model):
    first = upload(pdf("invoice_digital.pdf"))
    with database() as session:
        session.get(Document, uuid.UUID(first.json()["document_id"])).status = "rejected"
        session.commit()
    assert dup_flags(upload(pdf("invoice_digital_rescan.pdf"))) == []


def test_a_new_document_does_not_match_itself_in_its_own_answer(digital_model):
    assert dup_flags(upload(pdf("invoice_digital.pdf"))) == []


def test_the_same_file_twice_is_a_duplicate_file_not_a_duplicate_invoice(digital_model):
    data = pdf("invoice_digital.pdf")
    upload(data)
    second = upload(data)
    codes = [f["code"] for f in second.json()["flags"]]
    assert "duplicate_file" in codes
    assert "duplicate_invoice" not in codes


def test_an_unrelated_file_raises_no_duplicate_flag(database, monkeypatch):
    other = invoice_of("invoice_wrong_total.pdf")
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: DIGITAL)
    upload(pdf("invoice_digital.pdf"))
    monkeypatch.setattr(main, "extract_invoice", lambda data, settings=None, client=None: other)
    assert dup_flags(upload(pdf("invoice_wrong_total.pdf"))) == []


def revalidate(invoice: InvoiceData, document_id=None):
    params = {"document_id": document_id} if document_id else None
    r = client.post("/validate", json=invoice.model_dump(mode="json"), params=params)
    assert r.status_code == 200
    return dup_flags(r.json()["flags"])


def test_a_document_checked_with_its_own_id_does_not_match_itself(digital_model):
    first = upload(pdf("invoice_digital.pdf")).json()
    assert revalidate(DIGITAL, first["document_id"]) == []


def test_without_the_id_the_stored_copy_counts_as_a_match(digital_model):
    upload(pdf("invoice_digital.pdf"))
    assert len(revalidate(DIGITAL)) == 1


def test_the_rescan_checked_with_its_own_id_still_matches_the_original(digital_model):
    upload(pdf("invoice_digital.pdf"))
    rescan = upload(pdf("invoice_digital_rescan.pdf")).json()
    assert len(revalidate(DIGITAL, rescan["document_id"])) == 1


def test_changing_the_number_clears_the_flag(digital_model):
    upload(pdf("invoice_digital.pdf"))
    rescan = upload(pdf("invoice_digital_rescan.pdf")).json()
    assert len(revalidate(DIGITAL, rescan["document_id"])) == 1

    changed = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), "invoice_number": "KFD-2026-9999"})
    assert revalidate(changed, rescan["document_id"]) == []


def test_changing_the_number_back_brings_the_flag_back(digital_model):
    upload(pdf("invoice_digital.pdf"))
    rescan = upload(pdf("invoice_digital_rescan.pdf")).json()
    changed = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), "invoice_number": "KFD-2026-9999"})
    assert revalidate(changed, rescan["document_id"]) == []
    assert len(revalidate(DIGITAL, rescan["document_id"])) == 1


def test_changing_the_supplier_clears_the_flag_too(digital_model):
    upload(pdf("invoice_digital.pdf"))
    rescan = upload(pdf("invoice_digital_rescan.pdf")).json()
    changed = InvoiceData.model_validate({**DIGITAL.model_dump(mode="json"), "supplier": "Twiga Auto Spares"})
    assert revalidate(changed, rescan["document_id"]) == []


def test_text_typed_into_a_form_is_matched_too(digital_model):
    upload(pdf("invoice_digital.pdf"))
    r = client.post("/validate", json={**DIGITAL.model_dump(mode="json"), "invoice_number": "  kfd-2026-0412 "})
    assert len(dup_flags(r.json()["flags"])) == 1


def test_an_id_that_is_not_stored_is_simply_not_excluded(digital_model):
    upload(pdf("invoice_digital.pdf"))
    assert len(revalidate(DIGITAL, str(uuid.uuid4()))) == 1


def test_an_id_that_is_not_a_uuid_is_refused():
    r = client.post("/validate?document_id=not-an-id", json={})
    assert r.status_code == 422