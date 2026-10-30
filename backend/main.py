from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import get_settings
from extractor import ExtractionError, extract_invoice
from pdf_reader import PdfReadError
from schemas import Flag, InvoiceData
from security import AccessKeyMiddleware
from suppliers import SupplierListError, load_known_suppliers
from validation import validate

settings = get_settings()

PUBLIC_PATHS = {"/health"}
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

try:
    known_suppliers = load_known_suppliers(settings.known_suppliers_file)
except SupplierListError as exc:
    raise SystemExit(f"\nDocFlow cannot start: {exc}\n")

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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PDF_MAGIC = b"%PDF-"


class ExtractResponse(BaseModel):
    filename: str
    invoice: InvoiceData
    flags: list[Flag] = Field(default_factory=list)


class ValidateResponse(BaseModel):
    flags: list[Flag] = Field(default_factory=list)


def check_invoice(invoice: InvoiceData) -> list[Flag]:
    return validate(
        invoice,
        max_age_days=settings.max_invoice_age_days,
        tolerance=settings.validation_tolerance,
        known_suppliers=known_suppliers,
    )


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/auth/check")
def check_access():
    """Reaching this means the access key was accepted. The key check itself is the middleware's job."""
    return {"status": "ok"}

@app.post("/extract", response_model=ExtractResponse)
def extract(file: UploadFile | None = File(default=None)):
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

    try:
        invoice = extract_invoice(data, settings=settings)
    except PdfReadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ExtractResponse(
        filename=file.filename or "upload.pdf",
        invoice=invoice,
        flags=check_invoice(invoice),
    )


@app.post("/validate", response_model=ValidateResponse)
def validate_invoice(invoice: InvoiceData):
    """Re-check an invoice the user has edited. Takes the invoice as the request body."""
    return ValidateResponse(flags=check_invoice(invoice))