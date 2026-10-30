import type { Invoice } from "./api";

export type DraftLineItem = {
  description: string;
  quantity: string;
  unit_price: string;
  amount: string;
};

export type DraftTotals = {
  subtotal: string;
  tax: string;
  total: string;
};

export type Draft = {
  supplier: string;
  invoice_number: string;
  date: string;
  currency: string;
  line_items: DraftLineItem[];
  totals: DraftTotals;
};

function text(value: string | null): string {
  return value ?? "";
}

function money(value: number | null): string {
  return value === null ? "" : value.toFixed(2);
}

function quantity(value: number | null): string {
  return value === null ? "" : String(value);
}

export function toDraft(invoice: Invoice): Draft {
  return {
    supplier: text(invoice.supplier),
    invoice_number: text(invoice.invoice_number),
    date: text(invoice.date),
    currency: text(invoice.currency),
    line_items: invoice.line_items.map((item) => ({
      description: text(item.description),
      quantity: quantity(item.quantity),
      unit_price: money(item.unit_price),
      amount: money(item.amount),
    })),
    totals: {
      subtotal: money(invoice.totals.subtotal),
      tax: money(invoice.totals.tax),
      total: money(invoice.totals.total),
    },
  };
}