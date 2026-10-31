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