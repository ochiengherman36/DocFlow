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
  status: string;
  filename: string;
  invoice: Invoice;
  flags: Flag[];
};

export class ApiError extends Error {}

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