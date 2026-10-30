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

export type ExtractResult = {
  filename: string;
  invoice: Invoice;
};

export class ApiError extends Error {}

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
    res = await fetch(`${apiUrl}/extract`, { method: "POST", body });
  } catch {
    throw new ApiError("Could not reach the server. Check that the backend is running.");
  }

  if (!res.ok) throw new ApiError(await errorMessage(res));

  try {
    return (await res.json()) as ExtractResult;
  } catch {
    throw new ApiError("The server sent a response that could not be read.");
  }
}