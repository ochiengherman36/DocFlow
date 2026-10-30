import type { ExtractResult } from "@/lib/api";

function text(value: string | null) {
  return value ?? <span className="text-zinc-400">not found</span>;
}

function money(value: number | null) {
  if (value === null) return <span className="text-zinc-400">—</span>;
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function quantity(value: number | null) {
  if (value === null) return <span className="text-zinc-400">—</span>;
  return value.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

export default function InvoiceResult({ result }: { result: ExtractResult }) {
  const { invoice } = result;
  const currency = invoice.currency ?? "";

  return (
    <section className="mt-10 flex flex-col gap-6" aria-label="Extracted invoice">
      <h2 className="text-xl font-semibold">Extracted data</h2>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
        <dt className="font-medium">Supplier</dt>
        <dd>{text(invoice.supplier)}</dd>
        <dt className="font-medium">Invoice number</dt>
        <dd>{text(invoice.invoice_number)}</dd>
        <dt className="font-medium">Date</dt>
        <dd>{text(invoice.date)}</dd>
        <dt className="font-medium">Currency</dt>
        <dd>{text(invoice.currency)}</dd>
      </dl>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-2 pr-4">Description</th>
              <th className="py-2 pr-4 text-right">Qty</th>
              <th className="py-2 pr-4 text-right">Unit price</th>
              <th className="py-2 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {invoice.line_items.length === 0 && (
              <tr>
                <td colSpan={4} className="py-3 text-zinc-400">
                  No line items found.
                </td>
              </tr>
            )}
            {invoice.line_items.map((item, index) => (
              <tr key={index} className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-2 pr-4">{text(item.description)}</td>
                <td className="py-2 pr-4 text-right">{quantity(item.quantity)}</td>
                <td className="py-2 pr-4 text-right">{money(item.unit_price)}</td>
                <td className="py-2 text-right">{money(item.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <dl className="ml-auto grid grid-cols-[auto_auto] gap-x-8 gap-y-1 text-right">
        <dt>Subtotal {currency}</dt>
        <dd>{money(invoice.totals.subtotal)}</dd>
        <dt>Tax {currency}</dt>
        <dd>{money(invoice.totals.tax)}</dd>
        <dt className="font-semibold">Total {currency}</dt>
        <dd className="font-semibold">{money(invoice.totals.total)}</dd>
      </dl>

      <details>
        <summary className="cursor-pointer text-sm text-zinc-600 dark:text-zinc-400">
          Raw JSON
        </summary>
        <pre className="mt-2 overflow-x-auto rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-900">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </section>
  );
}