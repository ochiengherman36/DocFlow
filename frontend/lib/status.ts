import type { DocumentStatus } from "./api";

const LABELS: Record<DocumentStatus, string> = {
  pending: "Pending review",
  approved: "Approved",
  rejected: "Rejected",
};

const HINTS: Record<DocumentStatus, string | null> = {
  pending: null,
  approved: "This invoice has already been approved.",
  rejected: "This invoice was rejected.",
};

export function statusLabel(status: DocumentStatus): string {
  return LABELS[status] ?? status;
}

export function statusHint(status: DocumentStatus): string | null {
  return HINTS[status] ?? null;
}