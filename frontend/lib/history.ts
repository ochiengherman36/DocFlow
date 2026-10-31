import type { AuditEntry } from "./api";

export type ChangeRow = { label: string; from: string; to: string };

const ACTIONS: Record<string, string> = {
  extracted: "Extracted",
  duplicate_upload: "Uploaded again",
  edited: "Edited",
  approved: "Approved",
  rejected: "Rejected",
};

const HEADER_FIELDS = ["supplier", "invoice_number", "date", "currency"];
const HEADER_LABELS: Record<string, string> = {
  supplier: "Supplier",
  invoice_number: "Invoice number",
  date: "Date",
  currency: "Currency",
};
const LINE_FIELDS = ["description", "quantity", "unit_price", "amount"];
const TOTAL_FIELDS = ["subtotal", "tax", "total"];

export function actionLabel(action: string): string {
  return ACTIONS[action] ?? action;
}

export function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function describeField(field: string): { rank: number; label: string } {
  if (HEADER_FIELDS.includes(field)) {
    return { rank: HEADER_FIELDS.indexOf(field), label: HEADER_LABELS[field] };
  }

  const total = /^totals\.(\w+)$/.exec(field);
  if (total) {
    const position = TOTAL_FIELDS.indexOf(total[1]);
    const name = total[1].charAt(0).toUpperCase() + total[1].slice(1);
    return { rank: 10_000 + (position < 0 ? 9 : position), label: name };
  }

  const line = /^line_items\[(\d+)\](?:\.(\w+))?$/.exec(field);
  if (line) {
    const index = Number(line[1]);
    const column = line[2];
    const position = column ? LINE_FIELDS.indexOf(column) : -1;
    return {
      rank: 100 + index * 10 + (position < 0 ? 0 : position + 1),
      label: column ? `Line ${index + 1} ${column.replace("_", " ")}` : `Line ${index + 1}`,
    };
  }

  return { rank: 99_999, label: field };
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "empty";
  if (typeof value === "number") return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const line = value as Record<string, unknown>;
    return `${formatValue(line.description)}, ${formatValue(line.quantity)} × ${formatValue(line.unit_price)} = ${formatValue(line.amount)}`;
  }
  return String(value);
}

export function entryChanges(entry: AuditEntry): ChangeRow[] {
  const changes = entry.details?.changes;
  if (!changes || typeof changes !== "object") return [];

  return Object.entries(changes as Record<string, { from?: unknown; to?: unknown }>)
    .map(([field, change]) => ({ ...describeField(field), from: formatValue(change?.from), to: formatValue(change?.to) }))
    .sort((a, b) => a.rank - b.rank)
    .map(({ label, from, to }) => ({ label, from, to }));
}

export function entrySummary(entry: AuditEntry): string | null {
  const details = entry.details ?? {};

  switch (entry.action) {
    case "extracted":
      return typeof details.filename === "string" ? `Read ${details.filename}` : null;
    case "duplicate_upload":
      return typeof details.filename === "string" ? `Uploaded again as ${details.filename}` : null;
    case "approved": {
      const parts: string[] = [];
      if (details.supplier_added === true) parts.push("New supplier added to the list");
      const warnings = Array.isArray(details.warnings) ? details.warnings.length : 0;
      if (warnings > 0) parts.push(`Approved with ${warnings} warning${warnings === 1 ? "" : "s"}`);
      return parts.length > 0 ? parts.join(". ") : null;
    }
    case "rejected":
      return typeof details.reason === "string" ? `Reason: ${details.reason}` : null;
    default:
      return null;
  }
}