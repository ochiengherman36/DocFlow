"use client";

import { useState } from "react";

import { ApiError, extractInvoice, type ExtractResult } from "@/lib/api";
import FlagList from "./flag-list";
import InvoiceResult from "./invoice-result";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadForm() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!API_URL) {
    return (
      <p role="alert" className="mt-8 rounded border border-red-500 p-4 text-red-600">
        NEXT_PUBLIC_API_URL is not set. Add it to frontend/.env.local and restart
        the dev server.
      </p>
    );
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      setResult(await extractInvoice(file, API_URL!));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
    setResult(null);
    setError(null);
  }

  return (
    <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4">
      <label htmlFor="invoice" className="font-medium">
        Invoice PDF
      </label>
      <input
        id="invoice"
        type="file"
        accept="application/pdf,.pdf"
        disabled={loading}
        onChange={handleFileChange}
        className="rounded border border-zinc-300 p-2 dark:border-zinc-700"
      />
      {file && (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {file.name} ({formatSize(file.size)})
        </p>
      )}
      <button
        type="submit"
        disabled={!file || loading}
        className="w-fit rounded bg-foreground px-5 py-2 font-medium text-background disabled:cursor-not-allowed disabled:opacity-40"
      >
        {loading ? "Reading invoice…" : "Upload"}
      </button>
      {loading && (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Scanned files can take 10 to 20 seconds.
        </p>
      )}
      {error && (
        <p role="alert" className="rounded border border-red-500 p-4 text-red-600">
          {error}
        </p>
      )}
      {result && (
        <>
           <FlagList flags={result.flags} />
          <InvoiceResult result={result} />
        </>
      )}
    </form>
  );
}