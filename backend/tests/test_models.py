import itertools
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import Base, make_engine
from models import AuditAction, AuditEntry, Document, DocumentStatus, Extraction, Supplier

INVOICE = {
    "supplier": "Kijani Fuel Distributors Ltd",
    "invoice_number": "KFD-2026-0412",
    "date": "2026-10-12",
    "currency": "KES",
    "line_items": [{"description": "Diesel", "quantity": 2000, "unit_price": 198.5, "amount": 397000}],
    "totals": {"subtotal": 397000, "tax": 63520, "total": 460520},
}

counter = itertools.count(1)


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_document(**changes) -> Document:
    n = next(counter)
    values = {
        "filename": f"invoice-{n}.pdf",
        "file_hash": f"{n:064x}",
        "file_size": 2048,
        "current_data": INVOICE,
        "supplier_key": "kijani fuel distributors",
        "invoice_number_key": f"kfd-2026-{n:04d}",
    }
    values.update(changes)
    return Document(**values)


def test_a_document_is_saved_and_read_back_with_its_defaults(session):
    session.add(make_document(filename="a.pdf", file_hash="a" * 64))
    session.commit()

    saved = session.scalars(select(Document)).one()
    assert isinstance(saved.id, uuid.UUID)
    assert saved.filename == "a.pdf"
    assert saved.file_hash == "a" * 64
    assert saved.status == DocumentStatus.PENDING
    assert saved.current_data == INVOICE
    assert saved.created_at is not None and saved.updated_at is not None
    assert saved.decided_by is None and saved.decided_at is None and saved.reject_reason is None


def test_json_comes_back_as_it_went_in(session):
    session.add(make_document())
    session.commit()
    session.expire_all()
    data = session.scalars(select(Document)).one().current_data
    assert data["totals"]["total"] == 460520
    assert data["line_items"][0]["unit_price"] == 198.5
    assert data["supplier"] == "Kijani Fuel Distributors Ltd"


def test_a_document_links_to_its_extraction_and_audit_trail(session):
    document = make_document()
    document.extraction = Extraction(data=INVOICE, model="claude-sonnet-5-5")
    session.add(document)
    session.flush()
    session.add_all(
        [
            AuditEntry(document_id=document.id, extraction_id=document.extraction.id,
                       action=AuditAction.EXTRACTED, actor="system", details={"filename": document.filename}),
            AuditEntry(document_id=document.id, action=AuditAction.EDITED, actor="Herman",
                       details={"changes": {"totals.total": {"from": 460520, "to": 460000}}}),
            AuditEntry(document_id=document.id, action=AuditAction.APPROVED, actor="Herman"),
        ]
    )
    session.commit()
    session.expire_all()

    saved = session.scalars(select(Document)).one()
    assert saved.extraction.data == INVOICE
    assert saved.extraction.model == "claude-sonnet-5-5"
    assert [entry.action for entry in saved.audit_entries] == ["extracted", "edited", "approved"]
    assert saved.audit_entries[0].extraction_id == saved.extraction.id
    assert saved.audit_entries[1].details["changes"]["totals.total"] == {"from": 460520, "to": 460000}
    assert saved.audit_entries[2].details is None
    assert all(entry.created_at is not None for entry in saved.audit_entries)


def test_audit_entries_get_increasing_ids(session):
    document = make_document()
    session.add(document)
    session.flush()
    entries = [AuditEntry(document_id=document.id, action="edited", actor="x") for _ in range(3)]
    session.add_all(entries)
    session.commit()
    ids = [entry.id for entry in entries]
    assert ids == sorted(ids) and len(set(ids)) == 3


def test_a_supplier_is_saved(session):
    session.add(Supplier(name="Kijani Fuel Distributors Ltd", name_key="kijani fuel distributors"))
    session.commit()
    saved = session.scalars(select(Supplier)).one()
    assert saved.id == 1 and saved.created_at is not None


def test_two_spellings_of_one_supplier_cannot_both_be_stored(session):
    session.add(Supplier(name="Kijani Fuel Distributors Ltd", name_key="kijani fuel distributors"))
    session.add(Supplier(name="Kijani Fuel Distributors Limited", name_key="kijani fuel distributors"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_document_can_point_at_a_supplier(session):
    supplier = Supplier(name="Kijani", name_key="kijani")
    session.add(supplier)
    session.flush()
    session.add(make_document(supplier_id=supplier.id))
    session.commit()
    assert session.scalars(select(Document)).one().supplier_id == supplier.id


def test_the_same_file_cannot_be_stored_twice(session):
    session.add(make_document(file_hash="b" * 64))
    session.add(make_document(file_hash="b" * 64))
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_bad_status_is_refused(session):
    session.add(make_document(status="finished"))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("status", [s.value for s in DocumentStatus])
def test_every_real_status_is_accepted(session, status):
    session.add(make_document(status=status))
    session.commit()


def test_a_document_has_only_one_extraction(session):
    document = make_document()
    session.add(document)
    session.flush()
    session.add(Extraction(document_id=document.id, data=INVOICE, model="m"))
    session.add(Extraction(document_id=document.id, data=INVOICE, model="m"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_an_audit_entry_needs_a_real_document(session):
    session.add(AuditEntry(document_id=uuid.uuid4(), action="edited", actor="x"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_document_with_history_cannot_be_deleted(session):
    document = make_document()
    session.add(document)
    session.flush()
    session.add(AuditEntry(document_id=document.id, action="extracted", actor="system"))
    session.commit()
    session.delete(document)
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_a_document_without_history_removes_its_extraction(session):
    document = make_document()
    document.extraction = Extraction(data=INVOICE, model="m")
    session.add(document)
    session.commit()
    session.execute(text("DELETE FROM documents"))
    session.commit()
    assert session.scalars(select(Extraction)).all() == []


SAME = {"supplier_key": "kijani", "invoice_number_key": "kfd-1"}


def test_two_approved_invoices_with_the_same_supplier_and_number_are_refused(session):
    session.add(make_document(status="approved", **SAME))
    session.add(make_document(status="approved", **SAME))
    with pytest.raises(IntegrityError):
        session.commit()


def test_an_approved_invoice_does_not_block_a_pending_copy(session):
    session.add(make_document(status="approved", **SAME))
    session.add(make_document(status="pending", **SAME))
    session.commit()


def test_an_approved_invoice_does_not_block_a_rejected_copy(session):
    session.add(make_document(status="approved", **SAME))
    session.add(make_document(status="rejected", **SAME))
    session.commit()


def test_many_pending_copies_are_allowed(session):
    session.add_all([make_document(status="pending", **SAME) for _ in range(3)])
    session.commit()


def test_the_same_number_from_another_supplier_is_fine(session):
    session.add(make_document(status="approved", supplier_key="kijani", invoice_number_key="kfd-1"))
    session.add(make_document(status="approved", supplier_key="twiga", invoice_number_key="kfd-1"))
    session.commit()


def test_another_number_from_the_same_supplier_is_fine(session):
    session.add(make_document(status="approved", supplier_key="kijani", invoice_number_key="kfd-1"))
    session.add(make_document(status="approved", supplier_key="kijani", invoice_number_key="kfd-2"))
    session.commit()


def test_a_pending_copy_cannot_be_approved_once_another_is_approved(session):
    first = make_document(status="approved", **SAME)
    second = make_document(status="pending", **SAME)
    session.add_all([first, second])
    session.commit()
    second.status = "approved"
    with pytest.raises(IntegrityError):
        session.commit()


def test_a_rejected_invoice_frees_its_supplier_and_number(session):
    first = make_document(status="approved", **SAME)
    session.add(first)
    session.commit()
    first.status = "rejected"
    session.commit()
    session.add(make_document(status="approved", **SAME))
    session.commit()