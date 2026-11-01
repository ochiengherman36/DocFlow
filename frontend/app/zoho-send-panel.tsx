"use client";

import { useState } from "react";

import { ApiError, sendToZoho } from "@/lib/api";
import { billOf, connectionHint, formatSentAt, type ZohoBill } from "@/lib/zoho";

const primary =
  "rounded bg-foreground px-5 py-2 font-medium text-background disabled:cursor-not-allowed disabled:opacity-40";

export default function ZohoSendPanel({
  apiUrl,
  documentId,
  initialBill,
  onSent,
}: {
  apiUrl: string;
  documentId: string;
  initialBill: ZohoBill | null;
  onSent: () => void;
}) {
  const [bill, setBill] = useState<ZohoBill | null>(initialBill);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await sendToZoho(apiUrl, documentId);
      setBill(billOf(result));
      if (result.already_sent) setNotice("This invoice was already sent. Here is its bill.");
      else if (result.recovered)
        setNotice("A bill for this invoice already existed in Zoho Books, so it was linked instead of making a second one.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
      onSent();
    }
  }

  const sentAt = formatSentAt(bill?.sentAt ?? null);
  const hint = error ? connectionHint(error) : null;

  return (
    <section aria-label="Zoho Books" className="flex flex-col gap-4 border-t border-zinc-300 pt-6 dark:border-zinc-700">
      <h2 className="text-xl font-semibold">Zoho Books</h2>

      {notice && (
        <p role="status" className="rounded border border-zinc-300 p-3 text-sm dark:border-zinc-700">
          {notice}
        </p>
      )}

      {error && (
        <div role="alert" className="flex flex-col gap-1 rounded border border-red-500 p-3 text-sm text-red-600">
          <p>{error}</p>
          {hint && <p>{hint}</p>}
        </div>
      )}

      {bill ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-green-600 p-3 text-sm">
          <span className="rounded-full border border-green-600 px-3 py-0.5 font-medium text-green-600">
            Sent to Zoho Books
          </span>
          {bill.sentBy && (
            <span className="text-zinc-600 dark:text-zinc-400">
              by {bill.sentBy}
              {sentAt ? ` · ${sentAt}` : ""}
            </span>
          )}
          {bill.url ? (
            <a
              href={bill.url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto font-medium text-green-600 underline"
            >
              Open in Zoho Books
            </a>
          ) : (
            <span className="ml-auto text-zinc-600 dark:text-zinc-400">Bill {bill.id}</span>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            This invoice is approved. Send it to Zoho Books as a bill.
          </p>
          <div>
            <button type="button" onClick={send} disabled={busy} className={primary}>
              {busy ? "Sending…" : "Send to Zoho"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}