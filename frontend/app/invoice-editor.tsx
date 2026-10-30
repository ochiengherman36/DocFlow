"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError, validateInvoice, type Flag, type Invoice } from "@/lib/api";
import { toDraft, type Draft, type DraftLineItem, type DraftTotals } from "@/lib/draft";
import FlagList from "./flag-list";

const RECHECK_DELAY_MS = 400;

const inputClass =
  "w-full rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700";
const numberClass = `${inputClass} text-right`;

type HeaderField = "supplier" | "invoice_number" | "date" | "currency";

const HEADER_FIELDS: { name: HeaderField; label: string; type: "text" | "date" }[] = [
  { name: "supplier", label: "Supplier", type: "text" },
  { name: "invoice_number", label: "Invoice number", type: "text" },
  { name: "date", label: "Date", type: "date" },
  { name: "currency", label: "Currency", type: "text" },
];

const LINE_FIELDS: { name: keyof DraftLineItem; label: string }[] = [
  { name: "description", label: "description" },
  { name: "quantity", label: "quantity" },
  { name: "unit_price", label: "unit price" },
  { name: "amount", label: "amount" },
];

const TOTAL_FIELDS: { name: keyof DraftTotals; label: string }[] = [
  { name: "subtotal", label: "Subtotal" },
  { name: "tax", label: "Tax" },
  { name: "total", label: "Total" },
];

export default function InvoiceEditor({
  invoice,
  initialFlags,
  apiUrl,
}: {
  invoice: Invoice;
  initialFlags: Flag[];
  apiUrl: string;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(invoice));
  const [flags, setFlags] = useState<Flag[]>(initialFlags);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  // Stop any waiting or running check when the editor goes away.
  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
      inFlight.current?.abort();
    };
  }, []);

  function scheduleCheck(next: Draft) {
    if (timer.current) clearTimeout(timer.current);
    inFlight.current?.abort();
    setChecking(true);

    timer.current = setTimeout(async () => {
      const controller = new AbortController();
      inFlight.current = controller;
      try {
        const fresh = await validateInvoice(next, apiUrl, controller.signal);
        if (controller.signal.aborted) return;
        setFlags(fresh);
        setCheckError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setCheckError(err instanceof ApiError ? err.message : "Something went wrong.");
      } finally {
        if (!controller.signal.aborted) setChecking(false);
      }
    }, RECHECK_DELAY_MS);
  }

  function change(next: Draft) {
    setDraft(next);
    scheduleCheck(next);
  }

  function setHeader(field: HeaderField, value: string) {
    change({ ...draft, [field]: value });
  }

  function setLine(index: number, field: keyof DraftLineItem, value: string) {
    change({
      ...draft,
      line_items: draft.line_items.map((item, i) => (i === index ? { ...item, [field]: value } : item)),
    });
  }

  function setTotal(field: keyof DraftTotals, value: string) {
    change({ ...draft, totals: { ...draft.totals, [field]: value } });
  }

  return (
    <>
      <div className="flex flex-col gap-2">
        <div className={checking ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <FlagList flags={flags} />
        </div>
        <p aria-live="polite" className="min-h-5 text-sm text-zinc-600 dark:text-zinc-400">
          {checking ? "Updating checks…" : ""}
        </p>
        {checkError && (
          <p role="alert" className="rounded border border-amber-500 p-3 text-sm text-amber-600">
            Could not update the checks: {checkError} The checks above may be out of date.
          </p>
        )}
      </div>

      <section className="flex flex-col gap-6" aria-label="Extracted invoice">
        <h2 className="text-xl font-semibold">Extracted data</h2>

        <div className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-3">
          {HEADER_FIELDS.map((field) => (
            <div key={field.name} className="contents">
              <label htmlFor={`invoice-${field.name}`} className="font-medium">
                {field.label}
              </label>
              <input
                id={`invoice-${field.name}`}
                type={field.type}
                value={draft[field.name]}
                onChange={(event) =>
                  setHeader(
                    field.name,
                    field.name === "currency" ? event.target.value.toUpperCase() : event.target.value,
                  )
                }
                maxLength={field.name === "currency" ? 3 : undefined}
                className={inputClass}
              />
            </div>
          ))}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-2 pr-2">Description</th>
                <th className="py-2 pr-2 text-right">Qty</th>
                <th className="py-2 pr-2 text-right">Unit price</th>
                <th className="py-2 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {draft.line_items.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-3 text-zinc-400">
                    No line items found.
                  </td>
                </tr>
              )}
              {draft.line_items.map((item, index) => (
                <tr key={index} className="border-b border-zinc-200 dark:border-zinc-800">
                  {LINE_FIELDS.map((field) => (
                    <td key={field.name} className="py-2 pr-2 last:pr-0">
                      <input
                        type="text"
                        inputMode={field.name === "description" ? "text" : "decimal"}
                        aria-label={`Line ${index + 1} ${field.label}`}
                        value={item[field.name]}
                        onChange={(event) => setLine(index, field.name, event.target.value)}
                        className={
                          field.name === "description"
                            ? `${inputClass} min-w-32`
                            : `${numberClass} ${field.name === "quantity" ? "w-20" : "w-28"}`
                        }
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="ml-auto grid grid-cols-[auto_9rem] items-center gap-x-4 gap-y-2">
          {TOTAL_FIELDS.map((field) => (
            <div key={field.name} className="contents">
              <label htmlFor={`totals-${field.name}`} className="text-right">
                {field.label} {draft.currency}
              </label>
              <input
                id={`totals-${field.name}`}
                type="text"
                inputMode="decimal"
                value={draft.totals[field.name]}
                onChange={(event) => setTotal(field.name, event.target.value)}
                className={numberClass}
              />
            </div>
          ))}
        </div>

        <details>
          <summary className="cursor-pointer text-sm text-zinc-600 dark:text-zinc-400">
            Current values as JSON
          </summary>
          <pre className="mt-2 overflow-x-auto rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-900">
            {JSON.stringify(draft, null, 2)}
          </pre>
        </details>
      </section>
    </>
  );
}