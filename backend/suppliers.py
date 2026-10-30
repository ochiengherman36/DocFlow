import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from rapidfuzz import fuzz, process

DEFAULT_SUPPLIERS_FILE = Path(__file__).parent / "known_suppliers.json"

# 0 to 100. At or above this, a name that is not an exact match is treated as a misspelling.
NEAR_MATCH_THRESHOLD = 85

# Words that differ between writings of the same company name ("Ltd" and "Limited").
_COMPANY_WORDS = frozenset({"ltd", "limited", "plc", "inc", "llc", "co", "company", "corp", "corporation"})


class SupplierListError(Exception):
    """The supplier list file is missing or not in the expected format."""


@dataclass(frozen=True)
class SupplierMatch:
    status: Literal["exact", "near", "unknown"]
    stored_name: str | None = None
    score: float = 0.0


def load_known_suppliers(path: Path = DEFAULT_SUPPLIERS_FILE) -> list[str]:
    """Read the stored supplier names. The file is a JSON list of strings."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SupplierListError(f"Supplier list not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SupplierListError(f"Supplier list is not valid JSON: {path} ({exc.msg})") from exc

    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise SupplierListError(f"Supplier list must be a JSON list of names: {path}")

    names: list[str] = []
    for item in raw:
        name = item.strip()
        if name and name not in names:
            names.append(name)
    return names


def normalise_name(name: str) -> str:
    """Lower-case, drop punctuation and company words, so trivial differences do not count."""
    text = re.sub(r"[^\w\s]", " ", name.lower())
    words = [word for word in text.split() if word not in _COMPANY_WORDS]
    return " ".join(words) or " ".join(text.split())


def match_supplier(
    name: str,
    known: Sequence[str],
    threshold: float = NEAR_MATCH_THRESHOLD,
) -> SupplierMatch:
    """Compare a name with the stored suppliers: exact, a near match, or unknown."""
    if not known:
        return SupplierMatch("unknown")

    target = normalise_name(name)
    choices = {stored: normalise_name(stored) for stored in known}

    for stored, normalised in choices.items():
        if normalised == target:
            return SupplierMatch("exact", stored, 100.0)

    _, score, stored = process.extractOne(target, choices, scorer=fuzz.token_sort_ratio)
    if score >= threshold:
        return SupplierMatch("near", stored, score)
    return SupplierMatch("unknown", stored, score)