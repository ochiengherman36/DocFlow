import base64
import json
from io import BytesIO

import anthropic
from pypdf import PdfReader, PdfWriter

from config import Settings, get_settings
from pdf_reader import PageText, read_pdf_pages
from schemas import InvoiceData

MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """You extract data from supplier invoices.

Rules:
- Return only what is printed on the document. Never guess, infer or calculate a value that is not shown.
- If a field is missing, unreadable or you are not sure, use null for it. A null is always better than a wrong value.
- Copy supplier name and invoice number exactly as printed.
- date must be ISO format YYYY-MM-DD. currency must be a 3-letter code such as KES, USD or EUR.
- Numbers must be plain numbers with no currency symbol and no thousands separators.
- line_items lists every line in the table, in order, with description, quantity, unit_price and amount as printed.
- totals holds subtotal, tax and total exactly as printed on the invoice. Do not recompute them.
- Ignore bank details, payment terms and footers."""

USER_INSTRUCTION = "Extract the invoice data from the document below."


class ExtractionError(Exception):
    """Extraction failed. The message is safe to show to the user."""


def _json_schema() -> dict:
    schema = InvoiceData.model_json_schema()
    return anthropic.transform_schema(schema)


def _pdf_with_pages(pdf_bytes: bytes, page_numbers: list[int]) -> bytes:
    """Return a new PDF that contains only the given 1-based pages."""
    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()
    for number in page_numbers:
        writer.add_page(reader.pages[number - 1])
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def build_content(pdf_bytes: bytes, pages: list[PageText]) -> list[dict]:
    """Pages with text go as text. Pages without text go as a PDF of just those pages."""
    content: list[dict] = []

    scanned = [p.number for p in pages if not p.has_text]
    if scanned:
        data = base64.standard_b64encode(_pdf_with_pages(pdf_bytes, scanned)).decode("ascii")
        content.append(
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            }
        )

    parts = [USER_INSTRUCTION]
    if scanned:
        parts.append(
            "The attached PDF contains the scanned pages "
            f"({', '.join(str(n) for n in scanned)} of the original). Read them as images."
        )
    for page in pages:
        if page.has_text:
            parts.append(f"--- Page {page.number} (text) ---\n{page.text}")
    content.append({"type": "text", "text": "\n\n".join(parts)})
    return content


def extract_invoice(
    pdf_bytes: bytes,
    settings: Settings | None = None,
    client: anthropic.Anthropic | None = None,
) -> InvoiceData:
    settings = settings or get_settings()
    pages = read_pdf_pages(pdf_bytes)
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_content(pdf_bytes, pages)}],
            output_config={"format": {"type": "json_schema", "schema": _json_schema()}},
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from exc
    except anthropic.NotFoundError as exc:
        raise ExtractionError(f"Model '{settings.anthropic_model}' was not found. Check ANTHROPIC_MODEL.") from exc
    except anthropic.RateLimitError as exc:
        raise ExtractionError("Rate limited by the Anthropic API. Try again in a moment.") from exc
    except anthropic.BadRequestError as exc:
        raise ExtractionError(f"Claude rejected the request: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError("Could not reach the Anthropic API. Check the network.") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Anthropic API error ({exc.status_code}). Try again later.") from exc

    if response.stop_reason == "refusal":
        raise ExtractionError("Claude declined to process this document.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The invoice was too long to extract in one pass.")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("Claude returned no data.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError("Claude returned data that could not be read.") from exc
    return InvoiceData.model_validate(raw)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    for arg in sys.argv[1:]:
        path = Path(arg)
        result = extract_invoice(path.read_bytes())
        print(f"\n===== {path.name} =====")
        print(result.model_dump_json(indent=2))