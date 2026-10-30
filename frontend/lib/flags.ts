import type { Flag } from "./api";
import type { Draft } from "./draft";

export type Tone = "error" | "warning" | "none";

export function groupByField(flags: Flag[]): Map<string, Flag[]> {
  const groups = new Map<string, Flag[]>();
  for (const flag of flags) {
    const list = groups.get(flag.field);
    if (list) list.push(flag);
    else groups.set(flag.field, [flag]);
  }
  return groups;
}

export function toneOf(flags: Flag[] | undefined): Tone {
  if (!flags || flags.length === 0) return "none";
  return flags.some((flag) => flag.severity === "error") ? "error" : "warning";
}

export function lineField(index: number, field: string): string {
  return `line_items[${index}].${field}`;
}

export function flagId(field: string): string {
  return `flags-${field.replace(/[^a-zA-Z0-9]+/g, "-").replace(/-+$/, "")}`;
}

const LINE_FIELDS = ["description", "quantity", "unit_price", "amount"];

export function visibleFields(draft: Draft): Set<string> {
  const fields = new Set<string>([
    "supplier",
    "invoice_number",
    "date",
    "currency",
    "totals.subtotal",
    "totals.tax",
    "totals.total",
    "line_items",
  ]);
  draft.line_items.forEach((_, index) => {
    for (const name of LINE_FIELDS) fields.add(lineField(index, name));
  });
  return fields;
}

export function unplacedFlags(flags: Flag[], draft: Draft): Flag[] {
  const visible = visibleFields(draft);
  return flags.filter((flag) => !visible.has(flag.field));
}