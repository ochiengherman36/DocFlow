from pathlib import Path

import pytest
from pydantic import ValidationError

from config import BASE_DIR, Settings

REQUIRED = {
    "anthropic_api_key": "k",
    "anthropic_model": "m",
    "frontend_origin": "http://localhost:3000",
}


def make(**extra) -> Settings:
    return Settings(_env_file=None, **REQUIRED, **extra)


def test_defaults(monkeypatch):
    monkeypatch.delenv("MAX_INVOICE_AGE_DAYS", raising=False)
    s = make()
    assert s.max_invoice_age_days == 365
    assert s.validation_tolerance == 0.01
    assert s.known_suppliers_file == BASE_DIR / "known_suppliers.json"


@pytest.mark.parametrize("name, value", [("max_invoice_age_days", 0), ("max_upload_mb", 0), ("validation_tolerance", -0.5)])
def test_bad_values_are_rejected(name, value):
    with pytest.raises(ValidationError):
        make(**{name: value})


def test_zero_tolerance_is_allowed():
    assert make(validation_tolerance=0).validation_tolerance == 0


def test_relative_supplier_file_is_resolved_against_the_backend_folder():
    assert make(known_suppliers_file="data/my_suppliers.json").known_suppliers_file == BASE_DIR / "data" / "my_suppliers.json"


def test_absolute_supplier_file_is_kept(tmp_path):
    path = Path(tmp_path) / "s.json"
    assert make(known_suppliers_file=path).known_suppliers_file == path