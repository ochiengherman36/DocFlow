import type { Draft } from "./draft";
import { clearAccessKey, getAccessKey } from "./session";

export type LineItem = {
  description: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount: number | null;
};

export type Totals = {
  subtotal: number | null;
  tax: number | null;
  total: number | null;
};

export type Invoice = {
  supplier: string | null;
  invoice_number: string | null;
  date: string | null;
  currency: string | null;
  line_items: LineItem[];
  totals: Totals;
};

export type Severity = "error" | "warning";
export type DocumentStatus = "pending" | "approved" | "rejected";

export type FlagCode =
  | "missing_field"
  | "no_line_items"
  | "line_amount_mismatch"
  | "subtotal_mismatch"
  | "total_mismatch"
  | "negative_value"
  | "date_in_future"
  | "date_too_old"
  | "unknown_currency"
  | "supplier_unknown"
  | "supplier_near_match"
  | "duplicate_file"
  | "duplicate_invoice";

export type Flag = {
  code: FlagCode;
  severity: Severity;
  field: string;
  message: string;
};

export type ExtractResult = {
  document_id: string;
  status: DocumentStatus;
  filename: string;
  invoice: Invoice;
  flags: Flag[];
  zoho_bill_id?: string | null;
  zoho_bill_url?: string | null;
  zoho_sent_at?: string | null;
  zoho_sent_by?: string | null;
};

export type SaveResult = {
  document_id: string;
  status: DocumentStatus;
  invoice: Invoice;
  flags: Flag[];
  changes: { field: string; from: unknown; to: unknown }[];
};

export type DecisionResult = {
  document_id: string;
  status: DocumentStatus;
  decided_by: string;
  decided_at: string;
  reject_reason: string | null;
  supplier_added: boolean;
  flags: Flag[];
};

export type AuditEntry = {
  id: number;
  action: string;
  actor: string;
  at: string;
  details: Record<string, unknown> | null;
};

export type ZohoStatus = {
  configured: boolean;
  connected: boolean;
  connected_by: string | null;
  connected_at: string | null;
  api_domain: string | null;
  message: string | null;
};

export type DisconnectResult = {
  was_connected: boolean;
  revoked: boolean;
};

export type SendResult = {
  document_id: string;
  zoho_bill_id: string;
  zoho_bill_url: string | null;
  zoho_sent_at: string | null;
  zoho_sent_by: string | null;
  already_sent: boolean;
  recovered: boolean;
};

export class ApiError extends Error {}

export class ApprovalBlockedError extends ApiError {
  flags: Flag[];

  constructor(message: string, flags: Flag[]) {
    super(message);
    this.flags = flags;
  }
}

async function authedFetch(url: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const key = getAccessKey();
  if (key) headers.set("X-Access-Key", key);

  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) clearAccessKey("Your passcode was not accepted. Please enter it again.");
  return res;
}

export async function checkAccessKey(key: string, apiUrl: string): Promise<boolean> {
  let res: Response;
  try {
    res = await fetch(`${apiUrl}/auth/check`, { headers: { "X-Access-Key": key } });
  } catch {
    throw new ApiError("Could not reach the server. Check that the backend is running.");
  }
  if (res.ok) return true;
  if (res.status === 401) return false;
  throw new ApiError(await errorMessage(res));
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (typeof body?.detail?.message === "string") return body.detail.message;
  } catch {
    // The body was not JSON; fall through to the generic message.
  }
  return `The server returned an error (${res.status}).`;
}

export async function extractInvoice(file: File, apiUrl: string): Promise<ExtractResult> {
  const body = new FormData();
  body.append("file", file);

  let res: Response;
  try {
    res = await authedFetch(`${apiUrl}/extract`, { method: "POST", body });
  } catch {
    throw new ApiError("Could not reach the server. Check that the backend is running.");
  }

  if (!res.ok) throw new ApiError(await errorMessage(res));

  try {
    const data = (await res.json()) as ExtractResult;
    return { ...data, flags: data.flags ?? [] };
  } catch {
    throw new ApiError("The server sent a response that could not be read.");
  }
}

export async function validateInvoice(draft: Draft, apiUrl: string, documentId: string, signal?: AbortSignal): Promise<Flag[]> {
  let res: Response;
  try {
    res = await authedFetch(`${apiUrl}/validate?document_id=${encodeURIComponent(documentId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
      signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError("Could not reach the server. Check that the backend is running.");
  }

  if (!res.ok) throw new ApiError(await errorMessage(res));

  try {
    const data = (await res.json()) as { flags?: Flag[] };
    return data.flags ?? [];
  } catch {
    throw new ApiError("The server sent a response that could not be read.");
  }
}

async function send(url: string, init: RequestInit): Promise<Response> {
  try {
    return await authedFetch(url, init);
  } catch {
    throw new ApiError("Could not reach the server. Check that the backend is running.");
  }
}

async function readJson<T>(res: Response): Promise<T> {
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("The server sent a response that could not be read.");
  }
}

export async function saveDocument(draft: Draft, apiUrl: string, documentId: string): Promise<SaveResult> {
  const res = await send(`${apiUrl}/documents/${encodeURIComponent(documentId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  return readJson<SaveResult>(res);
}

export async function approveDocument(apiUrl: string, documentId: string): Promise<DecisionResult> {
  const res = await send(`${apiUrl}/documents/${encodeURIComponent(documentId)}/approve`, { method: "POST" });

  if (res.status === 409) {
    const body = await res.json().catch(() => null);
    if (Array.isArray(body?.detail?.flags)) {
      throw new ApprovalBlockedError(
        body.detail.message ?? "This invoice has errors and cannot be approved until they are fixed.",
        body.detail.flags,
      );
    }
    throw new ApiError(typeof body?.detail === "string" ? body.detail : "The server returned an error (409).");
  }

  if (!res.ok) throw new ApiError(await errorMessage(res));
  const data = await readJson<DecisionResult>(res);
  return { ...data, flags: data.flags ?? [] };
}

export async function rejectDocument(apiUrl: string, documentId: string, reason: string): Promise<DecisionResult> {
  const res = await send(`${apiUrl}/documents/${encodeURIComponent(documentId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  const data = await readJson<DecisionResult>(res);
  return { ...data, flags: data.flags ?? [] };
}

export async function fetchAudit(apiUrl: string, documentId: string): Promise<AuditEntry[]> {
  const res = await send(`${apiUrl}/documents/${encodeURIComponent(documentId)}/audit`, { method: "GET" });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  const data = await readJson<{ entries?: AuditEntry[] }>(res);
  return data.entries ?? [];
}

export async function fetchZohoStatus(apiUrl: string): Promise<ZohoStatus> {
  const res = await send(`${apiUrl}/integrations/zoho/status`, { method: "GET" });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  return readJson<ZohoStatus>(res);
}

export async function startZohoConnect(apiUrl: string): Promise<string> {
  const res = await send(`${apiUrl}/integrations/zoho/connect`, { method: "POST" });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  const data = await readJson<{ authorization_url?: string }>(res);
  if (!data.authorization_url) throw new ApiError("The server did not send the Zoho address.");
  return data.authorization_url;
}

export async function disconnectZoho(apiUrl: string): Promise<DisconnectResult> {
  const res = await send(`${apiUrl}/integrations/zoho`, { method: "DELETE" });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  return readJson<DisconnectResult>(res);
}

export async function sendToZoho(apiUrl: string, documentId: string): Promise<SendResult> {
  const res = await send(`${apiUrl}/documents/${encodeURIComponent(documentId)}/send-to-zoho`, { method: "POST" });
  if (!res.ok) throw new ApiError(await errorMessage(res));
  return readJson<SendResult>(res);
}