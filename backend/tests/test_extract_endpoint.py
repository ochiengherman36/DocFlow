from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from extractor import ExtractionError
from pdf_reader import PdfReadError
from schemas import InvoiceData

SAMPLES = Path(__file__).parent.parent / "samples"
HEADERS = {"X-Access-Key": main.settings.access_key.get_secret_value()}
client = TestClient(main.app, headers=HEADERS)


@pytest.fixture(autouse=True)
def fake_extract(monkeypatch):
    def fake(data, settings=None, client=None):
        return InvoiceData.model_validate({"supplier": "Test Supplier", "totals": {"total": 10}})

    monkeypatch.setattr(main, "extract_invoice", fake)


def post(name: str, content: bytes, content_type: str = "application/pdf"):
    return client.post("/extract", files={"file": (name, content, content_type)})


def test_valid_digital_pdf():
    r = post("invoice_digital.pdf", (SAMPLES / "invoice_digital.pdf").read_bytes())
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "invoice_digital.pdf"
    assert body["invoice"]["supplier"] == "Test Supplier"
    assert body["invoice"]["line_items"] == []
    # The fake invoice has only a supplier and a total, so validation must report what is missing.
    assert ("missing_field", "invoice_number") in [(f["code"], f["field"]) for f in body["flags"]]



def test_valid_scanned_pdf():
    r = post("invoice_scanned.pdf", (SAMPLES / "invoice_scanned.pdf").read_bytes())
    assert r.status_code == 200


def test_text_file_renamed_to_pdf_is_rejected():
    r = post("fake.pdf", b"this is just text, not a pdf")
    assert r.status_code == 400
    assert r.json()["detail"] == "Only PDF files are supported."


def test_png_is_rejected():
    r = post("photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")
    assert r.status_code == 400
    assert r.json()["detail"] == "Only PDF files are supported."


def test_real_pdf_with_wrong_extension_is_accepted():
    r = post("invoice.txt", (SAMPLES / "invoice_digital.pdf").read_bytes(), "text/plain")
    assert r.status_code == 200


def test_no_file():
    r = client.post("/extract")
    assert r.status_code == 400
    assert "No file uploaded" in r.json()["detail"]


def test_empty_file():
    r = post("empty.pdf", b"")
    assert r.status_code == 400
    assert r.json()["detail"] == "The uploaded file is empty."


def test_oversized_file():
    r = post("big.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024))
    assert r.status_code == 413
    assert "1 MB limit" in r.json()["detail"]


def test_corrupt_pdf_returns_400(monkeypatch):
    def boom(data, settings=None, client=None):
        raise PdfReadError("Could not read PDF: broken")

    monkeypatch.setattr(main, "extract_invoice", boom)
    r = post("broken.pdf", b"%PDF-1.4 broken")
    assert r.status_code == 400
    assert r.json()["detail"] == "Could not read PDF: broken"


def test_claude_failure_returns_502(monkeypatch):
    def boom(data, settings=None, client=None):
        raise ExtractionError("Rate limited by the Anthropic API. Try again in a moment.")

    monkeypatch.setattr(main, "extract_invoice", boom)
    r = post("invoice.pdf", (SAMPLES / "invoice_digital.pdf").read_bytes())
    assert r.status_code == 502
    assert "Rate limited" in r.json()["detail"]