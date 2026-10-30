"use client";

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadForm() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);

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
    try {
      // Placeholder: step A10 replaces this wait with the real call to `${API_URL}/extract`.
      await new Promise((resolve) => setTimeout(resolve, 1500));
    } finally {
      setLoading(false);
    }
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
        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
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
    </form>
  );
}