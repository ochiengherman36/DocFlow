from pathlib import Path

import pytest

from pdf_reader import PdfReadError, read_pdf_pages

SAMPLES = Path(__file__).parent.parent / "samples"


def test_digital_pdf_has_text():
    pages = read_pdf_pages(SAMPLES / "invoice_digital.pdf")
    assert len(pages) == 1
    assert pages[0].has_text
    assert "KFD-2026-0412" in pages[0].text


@pytest.mark.parametrize("name", ["invoice_scanned.pdf", "invoice_poor_quality.pdf"])
def test_scanned_pdf_has_no_text(name):
    pages = read_pdf_pages(SAMPLES / name)
    assert not pages[0].has_text


def test_accepts_bytes():
    data = (SAMPLES / "invoice_digital.pdf").read_bytes()
    assert read_pdf_pages(data)[0].has_text


@pytest.mark.parametrize("bad", [b"", b"this is not a pdf"])
def test_bad_input_raises_pdf_read_error(bad):
    with pytest.raises(PdfReadError):
        read_pdf_pages(bad)