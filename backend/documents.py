import uuid
import datetime
import copy
import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AuditAction, AuditEntry, Document, DocumentStatus, Extraction
from schemas import Flag, FlagCode, InvoiceData, Severity
from supplier_store import supplier_key
from validation import DuplicateMatch


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