from dataclasses import dataclass
from typing import Any

HEADER_FIELDS = ("supplier", "invoice_number", "date", "currency")
LINE_FIELDS = ("description", "quantity", "unit_price", "amount")
TOTAL_FIELDS = ("subtotal", "tax", "total")


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: Any
    new: Any


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= 1e-9
    return a == b


def diff_invoice(old: dict, new: dict) -> list[FieldChange]:
    changes: list[FieldChange] = []

    for name in HEADER_FIELDS:
        if not _same(old.get(name), new.get(name)):
            changes.append(FieldChange(name, old.get(name), new.get(name)))

    old_items = old.get("line_items") or []
    new_items = new.get("line_items") or []
    for index in range(max(len(old_items), len(new_items))):
        before = old_items[index] if index < len(old_items) else None
        after = new_items[index] if index < len(new_items) else None
        if before is None or after is None:
            changes.append(FieldChange(f"line_items[{index}]", before, after))
            continue
        for name in LINE_FIELDS:
            if not _same(before.get(name), after.get(name)):
                changes.append(FieldChange(f"line_items[{index}].{name}", before.get(name), after.get(name)))

    old_totals = old.get("totals") or {}
    new_totals = new.get("totals") or {}
    for name in TOTAL_FIELDS:
        if not _same(old_totals.get(name), new_totals.get(name)):
            changes.append(FieldChange(f"totals.{name}", old_totals.get(name), new_totals.get(name)))

    return changes