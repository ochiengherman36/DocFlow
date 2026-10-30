from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

MIN_TEXT_CHARS = 30


class PdfReadError(Exception):
    """The file could not be read as a PDF."""


@dataclass
class PageText:
    number: int
    text: str
    has_text: bool


def read_pdf_pages(source: bytes | str | Path, min_chars: int = MIN_TEXT_CHARS) -> list[PageText]:
    """Extract text from every page and flag pages that have real text.

    A page "has text" when it holds at least `min_chars` non-whitespace
    characters. Scanned pages have none, so they can be sent to Claude as
    images instead.
    """
    stream = BytesIO(source) if isinstance(source, bytes) else str(source)
    try:
        reader = PdfReader(stream)
        if reader.is_encrypted:
            raise PdfReadError("PDF is password protected")
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append(PageText(number=index, text=text, has_text=len("".join(text.split())) >= min_chars))
    except PdfReadError:
        raise
    except (PyPdfError, ValueError, OSError) as exc:
        raise PdfReadError(f"Could not read PDF: {exc}") from exc
    if not pages:
        raise PdfReadError("PDF has no pages")
    return pages


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        pages = read_pdf_pages(Path(arg))
        for p in pages:
            chars = len("".join(p.text.split()))
            print(f"{Path(arg).name} page {p.number}: has_text={p.has_text} chars={chars}")