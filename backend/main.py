import datetime
import uuid
import logging
from urllib.parse import urlencode
from typing import Any, Literal
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from crypto import TokenDecryptionError, get_cipher

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
    approve_document,
    reject_document,
    audit_trail,
    list_documents,
)
from extractor import ExtractionError, extract_invoice
from pdf_reader import PdfReadError
from schemas import Flag, InvoiceData, Severity
from security import AccessKeyMiddleware
from supplier_store import list_supplier_names
from validation import validate
from zoho_books import list_organizations
from zoho_oauth import (
    STATE_TTL,
    ZohoError,
    authorization_url,
    consume_state,
    create_state,
    exchange_code,
    revoke_token,
)
from zoho_store import delete_tokens, get_tokens, save_tokens
from zoho_tokens import ZohoNotConnected, get_access_token

settings = get_settings()
logger = logging.getLogger("docflow")

ZOHO_CALLBACK_PATH = "/integrations/zoho/callback"
PUBLIC_PATHS = {"/health", ZOHO_CALLBACK_PATH}
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
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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

class RejectRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=1000)


class DecisionResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    decided_by: str
    decided_at: datetime.datetime
    reject_reason: str | None = None
    supplier_added: bool = False
    flags: list[Flag] = Field(default_factory=list)

class AuditItem(BaseModel):
    id: int
    action: str
    actor: str
    at: datetime.datetime
    details: dict[str, Any] | None = None


class AuditResponse(BaseModel):
    document_id: uuid.UUID
    status: str
    entries: list[AuditItem]


class DocumentItem(BaseModel):
    document_id: uuid.UUID
    filename: str
    status: str
    supplier: str | None = None
    invoice_number: str | None = None
    date: str | None = None
    currency: str | None = None
    total: float | None = None
    created_at: datetime.datetime
    decided_by: str | None = None
    decided_at: datetime.datetime | None = None
    reject_reason: str | None = None


class DocumentList(BaseModel):
    documents: list[DocumentItem]
    total: int

class ConnectResponse(BaseModel):
    authorization_url: str
    expires_in: int

class ZohoStatus(BaseModel):
    configured: bool
    connected: bool
    connected_by: str | None = None
    connected_at: datetime.datetime | None = None
    api_domain: str | None = None
    message: str | None = None


class DisconnectResponse(BaseModel):
    was_connected: bool
    revoked: bool


class OrganizationItem(BaseModel):
    organization_id: str
    name: str
    is_default: bool
    currency_code: str | None = None


class OrganizationsResponse(BaseModel):
    organizations: list[OrganizationItem]

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

def load_pending(session: Session, document_id: uuid.UUID):
    """Load and lock a document for a decision. Only a pending one can be decided."""
    document = get_document(session, document_id, for_update=True)
    if document is None:
        raise HTTPException(status_code=404, detail="That document was not found.")
    if document.status != "pending":
        raise HTTPException(status_code=409, detail=f"This invoice was already {document.status}.")
    return document


@app.post("/documents/{document_id}/approve", response_model=DecisionResponse)
def approve(
    document_id: uuid.UUID,
    session: Session = Depends(get_db),
    actor: str = Depends(reviewer_name),
):
    """Approve the stored version. It is checked again here, so the screen cannot skip the checks.

    Errors refuse the approval with 409 and the flags. Warnings do not.
    """
    try:
        document = load_pending(session, document_id)
        invoice = InvoiceData.model_validate(document.current_data)
        flags = check_invoice(invoice, session, document.id)

        if any(f.severity == Severity.ERROR for f in flags):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "This invoice has errors and cannot be approved until they are fixed.",
                    "flags": [f.model_dump(mode="json") for f in flags],
                },
            )

        supplier_added = approve_document(session, document, invoice, actor, flags)
        session.commit()
    except IntegrityError as exc:
        # The unique index on approved invoices: another copy was approved after our check.
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The same invoice from this supplier was approved a moment ago. Refresh and check again.",
                "flags": [],
            },
        ) from exc
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    return DecisionResponse(
        document_id=document.id,
        status=document.status,
        decided_by=document.decided_by,
        decided_at=document.decided_at,
        supplier_added=supplier_added,
        flags=flags,
    )


@app.post("/documents/{document_id}/reject", response_model=DecisionResponse)
def reject(
    document_id: uuid.UUID,
    body: RejectRequest,
    session: Session = Depends(get_db),
    actor: str = Depends(reviewer_name),
):
    """Reject a pending invoice. A reason is required."""
    try:
        document = load_pending(session, document_id)
        reject_document(session, document, body.reason, actor)
        session.commit()
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    return DecisionResponse(
        document_id=document.id,
        status=document.status,
        decided_by=document.decided_by,
        decided_at=document.decided_at,
        reject_reason=document.reject_reason,
    )

def document_item(document) -> DocumentItem:
    data = document.current_data or {}
    return DocumentItem(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        supplier=data.get("supplier"),
        invoice_number=data.get("invoice_number"),
        date=data.get("date"),
        currency=data.get("currency"),
        total=(data.get("totals") or {}).get("total"),
        created_at=document.created_at,
        decided_by=document.decided_by,
        decided_at=document.decided_at,
        reject_reason=document.reject_reason,
    )


@app.get("/documents", response_model=DocumentList)
def documents_list(
    status: Literal["pending", "approved", "rejected"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
):
    """A page of documents, newest first. `total` counts every match, not just this page."""
    try:
        documents, total = list_documents(session, status=status, limit=limit, offset=offset)
    except OperationalError as exc:
        raise database_unavailable(exc) from exc
    return DocumentList(documents=[document_item(d) for d in documents], total=total)


@app.get("/documents/{document_id}/audit", response_model=AuditResponse)
def document_audit(document_id: uuid.UUID, session: Session = Depends(get_db)):
    """What happened to a document, newest first: who did what, and when."""
    try:
        document = get_document(session, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="That document was not found.")
        entries = audit_trail(session, document_id)
    except OperationalError as exc:
        raise database_unavailable(exc) from exc
    return AuditResponse(
        document_id=document.id,
        status=document.status,
        entries=[
            AuditItem(id=e.id, action=e.action, actor=e.actor, at=e.created_at, details=e.details)
            for e in entries
        ],
    )

@app.post("/integrations/zoho/connect", response_model=ConnectResponse)
def zoho_connect(
    session: Session = Depends(get_db),
    actor: str = Depends(reviewer_name),
):
    """Start connecting Zoho. Returns the address to send the browser to."""
    if not settings.zoho_configured:
        raise HTTPException(status_code=503, detail="Zoho is not set up on this server.")

    try:
        state = create_state(session, actor)
        session.commit()
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    return ConnectResponse(
        authorization_url=authorization_url(settings, state),
        expires_in=int(STATE_TTL.total_seconds()),
    )

def zoho_result(**params: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.frontend_origin}/?{urlencode(params)}", status_code=303)


def zoho_failed(reason: str) -> RedirectResponse:
    logger.warning("Zoho connection failed: %s", reason)
    return zoho_result(zoho="error", reason=reason)


@app.get(ZOHO_CALLBACK_PATH)
def zoho_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: Session = Depends(get_db),
):
    """Where Zoho sends the browser back. It carries no access key, so the state is what protects it.

    Every outcome is a redirect to the frontend: ?zoho=connected, or ?zoho=error&reason=...
    """
    if not settings.zoho_configured:
        return zoho_failed("not_configured")

    try:
        started_by = consume_state(session, state)
        session.commit()
    except OperationalError:
        return zoho_failed("unavailable")
    if started_by is None:
        return zoho_failed("invalid_state")

    if error:
        return zoho_failed("denied")
    if not code:
        return zoho_failed("missing_code")

    try:
        tokens = exchange_code(settings, code)
    except ZohoError as exc:
        logger.warning("Zoho token exchange failed: %s", exc)
        return zoho_failed("exchange_failed")

    expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=tokens.expires_in)
    try:
        save_tokens(
            session,
            get_cipher(settings),
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=expires_at,
            scope=tokens.scope,
            api_domain=tokens.api_domain,
            connected_by=started_by,
        )
        session.commit()
    except OperationalError:
        return zoho_failed("unavailable")

    return zoho_result(zoho="connected")

@app.get("/integrations/zoho/status", response_model=ZohoStatus)
def zoho_status(session: Session = Depends(get_db)):
    """Whether Zoho is connected. It reads the saved connection and does not call Zoho."""
    if not settings.zoho_configured:
        return ZohoStatus(configured=False, connected=False)

    try:
        tokens = get_tokens(session, get_cipher(settings))
    except TokenDecryptionError:
        return ZohoStatus(
            configured=True,
            connected=False,
            message="The saved Zoho connection could not be read. Connect Zoho again.",
        )
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    if tokens is None:
        return ZohoStatus(configured=True, connected=False)
    return ZohoStatus(
        configured=True,
        connected=True,
        connected_by=tokens.connected_by,
        connected_at=tokens.connected_at,
        api_domain=tokens.api_domain,
    )


@app.delete("/integrations/zoho", response_model=DisconnectResponse)
def zoho_disconnect(session: Session = Depends(get_db)):
    """Disconnect Zoho. The token is revoked at Zoho if possible, and the saved connection is always removed."""
    revoked = False
    try:
        if settings.zoho_configured:
            try:
                tokens = get_tokens(session, get_cipher(settings))
            except TokenDecryptionError:
                tokens = None
            if tokens is not None:
                try:
                    revoke_token(settings, tokens.refresh_token)
                    revoked = True
                except ZohoError as exc:
                    logger.warning("Could not revoke the Zoho token: %s", exc)

        was_connected = delete_tokens(session)
        session.commit()
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    return DisconnectResponse(was_connected=was_connected, revoked=revoked)


@app.get("/integrations/zoho/organizations", response_model=OrganizationsResponse)
def zoho_organizations(session: Session = Depends(get_db)):
    """Ask Zoho Books for the organizations this connection can reach. Renews the token first if needed."""
    if not settings.zoho_configured:
        raise HTTPException(status_code=503, detail="Zoho is not set up on this server.")

    try:
        access_token = get_access_token(session, settings)
        tokens = get_tokens(session, get_cipher(settings))
    except ZohoNotConnected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ZohoError as exc:
        logger.warning("Could not renew the Zoho token: %s", exc)
        raise HTTPException(status_code=502, detail="Zoho did not renew the connection. Try again in a moment.") from exc
    except OperationalError as exc:
        raise database_unavailable(exc) from exc

    try:
        organizations = list_organizations(settings, access_token, tokens.api_domain if tokens else None)
    except ZohoError as exc:
        logger.warning("Zoho Books call failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return OrganizationsResponse(
        organizations=[
            OrganizationItem(
                organization_id=o.organization_id,
                name=o.name,
                is_default=o.is_default,
                currency_code=o.currency_code,
            )
            for o in organizations
        ]
    )