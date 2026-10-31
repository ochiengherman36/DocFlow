import uuid
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from main import ExtractResponse
from schemas import Flag, FlagCode, InvoiceData, Severity

SAMPLES = Path(__file__).parent.parent / "samples"


def test_flag_serialises_to_json():
    flag = Flag(
        code=FlagCode.TOTAL_MISMATCH,
        severity=Severity.ERROR,
        field="totals.total",
        message="Total does not match subtotal plus tax.",
    )
    assert json.loads(flag.model_dump_json()) == {
        "code": "total_mismatch",
        "severity": "error",
        "field": "totals.total",
        "message": "Total does not match subtotal plus tax.",
    }


def test_flag_builds_from_plain_strings():
    flag = Flag.model_validate(
        {"code": "supplier_near_match", "severity": "warning", "field": "supplier", "message": "Close match."}
    )
    assert flag.code is FlagCode.SUPPLIER_NEAR_MATCH
    assert flag.severity is Severity.WARNING


@pytest.mark.parametrize(
    "bad",
    [
        {"code": "made_up_code", "severity": "error", "field": "x", "message": "m"},
        {"code": "missing_field", "severity": "fatal", "field": "x", "message": "m"},
        {"code": "missing_field", "severity": "error", "message": "m"},
    ],
)
def test_invalid_flag_is_rejected(bad):
    with pytest.raises(ValidationError):
        Flag.model_validate(bad)


def test_response_has_empty_flags_by_default():
    response = ExtractResponse(document_id=uuid.uuid4(), status="pending", filename="a.pdf", invoice=InvoiceData())
    data = response.model_dump(mode="json")
    assert data["flags"] == []
    assert set(data) == {
        "document_id", "status", "filename", "invoice", "flags",
        "zoho_bill_id", "zoho_bill_url", "zoho_sent_at", "zoho_sent_by",
    }
    assert data["zoho_bill_id"] is None and data["zoho_bill_url"] is None


def test_response_serialises_flags():
    flag = Flag(code=FlagCode.MISSING_FIELD, severity=Severity.ERROR, field="date", message="No date.")
    response = ExtractResponse(
        document_id=uuid.uuid4(), status="pending", filename="a.pdf", invoice=InvoiceData(), flags=[flag]
    )
    assert response.model_dump(mode="json")["flags"] == [
        {"code": "missing_field", "severity": "error", "field": "date", "message": "No date."}
    ]

def test_expected_flags_file_uses_valid_codes():
    """Every flag listed in samples/expected_flags.json must be a flag the model can express."""
    expected = json.loads((SAMPLES / "expected_flags.json").read_text(encoding="utf-8"))
    for name, flags in expected.items():
        if name.startswith("_"):
            continue
        for entry in flags:
            Flag(code=entry["code"], severity=entry["severity"], field=entry["field"], message=entry["note"])