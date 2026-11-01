export type ZohoReturn = { kind: "connected" } | { kind: "error"; message: string };

const REASONS: Record<string, string> = {
  invalid_state: "The connection attempt expired or was not recognised. Please try connecting again.",
  denied: "Access was declined on the Zoho screen, so Zoho was not connected.",
  missing_code: "Zoho did not send back an authorisation code. Please try again.",
  exchange_failed: "Zoho did not accept the connection. Please try again.",
  not_configured: "Zoho is not set up on the server yet.",
  unavailable: "The database is not available. Please try again in a moment.",
};

const UNKNOWN_REASON = "Zoho could not be connected. Please try again.";

export function reasonMessage(reason: string | null): string {
  return (reason && REASONS[reason]) || UNKNOWN_REASON;
}

export function readZohoReturn(search: string): ZohoReturn | null {
  const params = new URLSearchParams(search);
  const result = params.get("zoho");
  if (result === "connected") return { kind: "connected" };
  if (result === "error") return { kind: "error", message: reasonMessage(params.get("reason")) };
  return null;
}

export function cleanedUrl(href: string): string {
  const url = new URL(href);
  url.searchParams.delete("zoho");
  url.searchParams.delete("reason");
  return `${url.pathname}${url.search}${url.hash}`;
}

export function formatConnectedAt(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export type ZohoBill = { id: string; url: string | null; sentAt: string | null; sentBy: string | null };

type BillFields = {
  zoho_bill_id?: string | null;
  zoho_bill_url?: string | null;
  zoho_sent_at?: string | null;
  zoho_sent_by?: string | null;
};

export function billOf(source: BillFields): ZohoBill | null {
  if (!source.zoho_bill_id) return null;
  const url = source.zoho_bill_url ?? null;
  return {
    id: source.zoho_bill_id,
    url: url && url.startsWith("https://") ? url : null,
    sentAt: source.zoho_sent_at ?? null,
    sentBy: source.zoho_sent_by ?? null,
  };
}

export function connectionHint(message: string): string | null {
  return /not connected|connect zoho again/i.test(message)
    ? "Use Connect Zoho at the top of the page, then try sending again."
    : null;
}

export function formatSentAt(value: string | null): string | null {
  return formatConnectedAt(value);
}