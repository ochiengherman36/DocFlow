"use client";

import { useEffect, useState } from "react";

import { ApiError, fetchAudit, type AuditEntry } from "@/lib/api";
import { actionLabel, entryChanges, entrySummary, formatTime } from "@/lib/history";

export default function HistoryPanel({
  apiUrl,
  documentId,
  version,
}: {
  apiUrl: string;
  documentId: string;
  version: number;
}) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAudit(apiUrl, documentId)
      .then((fresh) => {
        if (cancelled) return;
        setEntries(fresh);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Something went wrong.");
      });
    return () => {
      cancelled = true;
    };
  }, [apiUrl, documentId, version]);

  return (
    <details className="rounded border border-zinc-300 p-3 dark:border-zinc-700">
      <summary className="cursor-pointer font-medium">History{entries ? ` (${entries.length})` : ""}</summary>

      {error && (
        <p role="alert" className="mt-3 rounded border border-amber-500 p-3 text-sm text-amber-600">
          Could not load the history: {error}
        </p>
      )}
      {!entries && !error && <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>}

      {entries && (
        <ol className="mt-3 flex flex-col gap-4">
          {entries.map((entry) => {
            const summary = entrySummary(entry);
            const changes = entryChanges(entry);
            return (
              <li key={entry.id} className="flex flex-col gap-1 text-sm">
                <p>
                  <span className="font-semibold">{actionLabel(entry.action)}</span>
                  <span className="text-zinc-600 dark:text-zinc-400">
                    {" "}
                    by {entry.actor} · {formatTime(entry.at)}
                  </span>
                </p>
                {summary && <p className="text-zinc-600 dark:text-zinc-400">{summary}</p>}
                {changes.length > 0 && (
                  <ul className="flex flex-col gap-0.5">
                    {changes.map((change) => (
                      <li key={change.label}>
                        <span className="font-medium">{change.label}:</span> {change.from} → {change.to}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </details>
  );
}