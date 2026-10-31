import uuid
from typing import Any
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from config import get_settings
from db import get_db
from documents import (
    apply_edit,
    duplicate_file_flag,
    file_hash,
    find_by_hash,
    record_duplicate_upload,
    get_document,
    save_new_document,
    find_duplicate_invoices,
)
from extractor import ExtractionError, extract_invoice
from pdf_reader import PdfReadError
from schemas import Flag, InvoiceData
from security import AccessKeyMiddleware
from supplier_store import list_supplier_names
from validation import validate

settings = get_settings()

PUBLIC_PATHS = {"/health"}
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

app = FastAPI(
    title="DocFlow API",
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url="/redoc" if settings.enable_api_docs else None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)

app.add_middleware(
    AccessKeyMiddleware,
    access_key=settings.access_key.get_secret_value(),
    public_paths=PUBLIC_PATHS | DOCS_PATHS if settings.enable_api_docs else PUBLIC_PATHS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

PDF_MAGIC = b"%PDF-"


class ExtractResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    filename: str
    invoice: InvoiceData
    flags: list[Flag] = Field(default_factory=list)


class ValidateResponse(BaseModel):
    flags: list[Flag] = Field(default_factory=list)

class SaveResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    invoice: InvoiceData
    flags: list[Flag] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)

def check_invoice(
    invoice: InvoiceData, session: Session, exclude_document_id: uuid.UUID | None = None
) -> list[Flag]:
    try:
        known_suppliers = list_supplier_names(session)
        duplicates = find_duplicate_invoices(session, invoice, exclude_document_id)
    except OperationalError as exc:
        raise HTTPException(
            status_code=503, detail="The database is not available. Please try again in a moment."
        ) from exc
    return validate(
        invoice,
        max_age_days=settings.max_invoice_age_days,
        tolerance=settings.validation_tolerance,
        known_suppliers=known_suppliers,
        duplicates=duplicates,
    )

DEFAULT_REVIEWER = "reviewer"


def reviewer_name(x_reviewer: str | None = Header(default=None)) -> str:
    name = (x_reviewer or "").strip()[:100]
    return name or DEFAULT_REVIEWER


def database_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503, detail="The database is not available. Please try again in a moment."
    )


def stored_result(document, filename: str, session: Session, actor: str) -> ExtractResponse:
    record_duplicate_upload(session, document, filename=filename, actor=actor)
    session.commit()
    invoice = InvoiceData.model_validate(document.current_data)
    return ExtractResponse(
        document_id=document.id,
        status=document.status,
        filename=filename,
        invoice=invoice,
        flags=[duplicate_file_flag(document), *check_invoice(invoice, session, document.id)],
    )

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/auth/check")
def check_access():
    """Reaching this means the access key was accepted. The key check itself is the middleware's job."""
    return {"status": "ok"}

@app.post("/extract", response_model=ExtractResponse)
def extract(
    file: UploadFile | None = File(default=None),
    session: Session = Depends(get_db),
    actor: str = Depends(reviewer_name),
):
    if file is None:
        raise HTTPException(status_code=400, detail="No file uploaded. Send a PDF in the 'file' field.")

    limit = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)

    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"The file is larger than the {settings.max_upload_mb} MB limit.",
        )
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    filename = file.filename or "upload.pdf"
    digest = file_hash(data)

    try:
        existing = find_by_hash(session, digest)
    except OperationalError as exc:
        raise database_unavailable(exc) from exc
    if existing is not None:
        return stored_result(existing, filename, session, actor)
    session.rollback()

    try:
        invoice = extract_invoice(data, settings=settings)
    except PdfReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        document = save_new_document(
            session,
            filename=filename,
            digest=digest,
            size=len(data),
            invoice=invoice,
            model=settings.anthropic_model,
            actor=actor,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = find_by_hash(session, digest)
        if existing is None:
            raise
        return stored_result(existing, filename, session, actor)
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    return ExtractResponse(
        document_id=document.id,
        status=document.status,
        filename=filename,
        invoice=invoice,
        flags=check_invoice(invoice, session, document.id),
    )

@app.post("/validate", response_model=ValidateResponse)
def validate_invoice(
    invoice: InvoiceData,
    document_id: uuid.UUID | None = None,
    session: Session = Depends(get_db),
):
    """Re-check an invoice the user has edited. Takes the invoice as the request body."""
    return ValidateResponse(flags=check_invoice(invoice, session, document_id))

@app.put("/documents/{document_id}", response_model=SaveResponse)
def save_document(
    document_id: uuid.UUID,
    invoice: InvoiceData,
    session: Session = Depends(get_db),
    actor: str = Depends(reviewer_name),
):
    """Store the reviewer's version of an invoice. The body is the invoice, as for /validate."""
    try:
        document = get_document(session, document_id, for_update=True)
        if document is None:
            raise HTTPException(status_code=404, detail="That document was not found.")
        if document.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"This invoice was already {document.status} and can no longer be edited.",
            )
        changes = apply_edit(session, document, invoice, actor)
        session.commit()
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    stored = InvoiceData.model_validate(document.current_data)
    return SaveResponse(
        document_id=document.id,
        status=document.status,
        invoice=stored,
        flags=check_invoice(stored, session, document.id),
        changes=[{"field": c.field, "from": c.old, "to": c.new} for c in changes],
    )