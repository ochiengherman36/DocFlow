import uuid
import datetime
import copy
import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from models import AuditAction, AuditEntry, Document, DocumentStatus, Extraction
from schemas import Flag, FlagCode, InvoiceData, Severity
from supplier_store import add_supplier, supplier_key
from validation import DuplicateMatch
from diffs import FieldChange, diff_invoice


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def invoice_number_key(number: str | None) -> str | None:
    if not number:
        return None
    key = re.sub(r"[^a-z0-9]", "", number.lower())[:100]
    return key or None


def supplier_key_of(name: str | None) -> str | None:
    if not name or not name.strip():
        return None
    return supplier_key(name)[:255]

def get_document(session: Session, document_id: uuid.UUID, *, for_update: bool = False) -> Document | None:
    return session.get(Document, document_id, with_for_update=for_update)


def apply_edit(session: Session, document: Document, invoice: InvoiceData, actor: str) -> list[FieldChange]:
    new_data = invoice.model_dump(mode="json")
    changes = diff_invoice(document.current_data, new_data)
    if not changes:
        return []

    document.current_data = new_data
    document.supplier_key = supplier_key_of(invoice.supplier)
    document.invoice_number_key = invoice_number_key(invoice.invoice_number)
    session.add(
        AuditEntry(
            document_id=document.id,
            extraction_id=document.extraction.id if document.extraction else None,
            action=AuditAction.EDITED.value,
            actor=actor,
            details={"changes": {c.field: {"from": c.old, "to": c.new} for c in changes}},
        )
    )
    return changes

def approve_document(
    session: Session, document: Document, invoice: InvoiceData, actor: str, warnings: list[Flag]
) -> bool:
    """Mark a pending document approved and add its supplier to the list if it is new.

    Returns True when the supplier was new. The caller has already refused errors and commits.
    """
    supplier_added = False
    if invoice.supplier and invoice.supplier.strip():
        supplier, supplier_added = add_supplier(session, invoice.supplier)
        document.supplier_id = supplier.id

    document.status = DocumentStatus.APPROVED.value
    document.decided_by = actor
    document.decided_at = datetime.datetime.now(datetime.UTC)
    document.reject_reason = None
    session.add(
        AuditEntry(
            document_id=document.id,
            extraction_id=document.extraction.id if document.extraction else None,
            action=AuditAction.APPROVED.value,
            actor=actor,
            details={"supplier_added": supplier_added, "warnings": [f.code.value for f in warnings]},
        )
    )
    return supplier_added


def reject_document(session: Session, document: Document, reason: str, actor: str) -> None:
    """Mark a pending document rejected, with the reason. The caller commits."""
    document.status = DocumentStatus.REJECTED.value
    document.decided_by = actor
    document.decided_at = datetime.datetime.now(datetime.UTC)
    document.reject_reason = reason
    session.add(
        AuditEntry(
            document_id=document.id,
            extraction_id=document.extraction.id if document.extraction else None,
            action=AuditAction.REJECTED.value,
            actor=actor,
            details={"reason": reason},
        )
    )

def audit_trail(session: Session, document_id: uuid.UUID) -> list[AuditEntry]:
    """Every entry for one document, newest first. The id grows with each write, so it is the true order."""
    return list(
        session.scalars(
            select(AuditEntry).where(AuditEntry.document_id == document_id).order_by(AuditEntry.id.desc())
        )
    )


def list_documents(
    session: Session, *, status: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[list[Document], int]:
    """One page of documents, newest first, and how many match in all."""
    query = select(Document)
    count = select(func.count()).select_from(Document)
    if status is not None:
        query = query.where(Document.status == status)
        count = count.where(Document.status == status)
    page = session.scalars(query.order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset))
    return list(page), session.scalar(count)

def find_by_hash(session: Session, digest: str) -> Document | None:
    return session.scalars(select(Document).where(Document.file_hash == digest)).first()
def find_duplicate_invoices(
    session: Session, invoice: InvoiceData, exclude_id: uuid.UUID | None = None
) -> list[DuplicateMatch]:
    supplier = supplier_key_of(invoice.supplier)
    number = invoice_number_key(invoice.invoice_number)
    if supplier is None or number is None:
        return []

    query = select(Document).where(
        Document.supplier_key == supplier,
        Document.invoice_number_key == number,
        Document.status.in_([DocumentStatus.PENDING.value, DocumentStatus.APPROVED.value]),
    )
    if exclude_id is not None:
        query = query.where(Document.id != exclude_id)

    documents = sorted(
        session.scalars(query),
        key=lambda d: (d.status != DocumentStatus.APPROVED.value, d.created_at or datetime.datetime.min),
    )
    return [
        DuplicateMatch(
            status=d.status,
            filename=d.filename,
            when=(d.decided_at or d.created_at) if d.status == DocumentStatus.APPROVED.value else d.created_at,
        )
        for d in documents
    ]

def save_new_document(
    session: Session,
    *,
    filename: str,
    digest: str,
    size: int,
    invoice: InvoiceData,
    model: str,
    actor: str,
) -> Document:
    data = invoice.model_dump(mode="json")
    document = Document(
        filename=filename,
        file_hash=digest,
        file_size=size,
        status=DocumentStatus.PENDING.value,
        current_data=data,
        supplier_key=supplier_key_of(invoice.supplier),
        invoice_number_key=invoice_number_key(invoice.invoice_number),
    )
    document.extraction = Extraction(data=copy.deepcopy(data), model=model)
    session.add(document)
    session.flush()

    session.add(
        AuditEntry(
            document_id=document.id,
            extraction_id=document.extraction.id,
            action=AuditAction.EXTRACTED.value,
            actor=actor,
            details={"filename": filename, "file_size": size, "file_hash": digest, "model": model},
        )
    )
    return document


def record_duplicate_upload(session: Session, document: Document, *, filename: str, actor: str) -> None:
    session.add(
        AuditEntry(
            document_id=document.id,
            extraction_id=document.extraction.id if document.extraction else None,
            action=AuditAction.DUPLICATE_UPLOAD.value,
            actor=actor,
            details={"filename": filename, "original_filename": document.filename},
        )
    )


def duplicate_file_flag(document: Document) -> Flag:
    uploaded = document.created_at.strftime("%d %b %Y") if document.created_at else "an earlier date"
    return Flag(
        code=FlagCode.DUPLICATE_FILE,
        severity=Severity.WARNING,
        field="file",
        message=f"This file was already uploaded on {uploaded} (status: {document.status}). "
        "The stored result is shown instead of reading it again.",
    )