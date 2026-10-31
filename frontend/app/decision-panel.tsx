"use client";

import { useState } from "react";

export type Busy = "approving" | "rejecting" | null;

const primary =
  "rounded bg-foreground px-5 py-2 font-medium text-background disabled:cursor-not-allowed disabled:opacity-40";
const danger =
  "rounded border border-red-500 px-5 py-2 font-medium text-red-600 disabled:cursor-not-allowed disabled:opacity-40";
const quiet =
  "rounded border border-zinc-300 px-5 py-2 font-medium disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700";

export default function DecisionPanel({
  busy,
  error,
  onApprove,
  onReject,
}: {
  busy: Busy;
  error: string | null;
  onApprove: () => void;
  onReject: (reason: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const working = busy !== null;
  const trimmed = reason.trim();

  return (
    <section aria-label="Decision" className="flex flex-col gap-4 border-t border-zinc-300 pt-6 dark:border-zinc-700">
      <h2 className="text-xl font-semibold">Decision</h2>

      {error && (
        <p role="alert" className="rounded border border-red-500 p-3 text-sm text-red-600">
          {error}
        </p>
      )}

      {!rejecting && (
        <div className="flex flex-wrap gap-3">
          <button type="button" onClick={onApprove} disabled={working} className={primary}>
            {busy === "approving" ? "Approving…" : "Approve"}
          </button>
          <button type="button" onClick={() => setRejecting(true)} disabled={working} className={danger}>
            Reject…
          </button>
        </div>
      )}

      {rejecting && (
        <div className="flex flex-col gap-2">
          <label htmlFor="reject-reason" className="font-medium">
            Reason for rejecting
          </label>
          <textarea
            id="reject-reason"
            rows={3}
            maxLength={1000}
            value={reason}
            disabled={working}
            onChange={(event) => setReason(event.target.value)}
            className="rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700"
          />
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => onReject(trimmed)}
              disabled={working || trimmed === ""}
              className={danger}
            >
              {busy === "rejecting" ? "Rejecting…" : "Confirm rejection"}
            </button>
            <button type="button" onClick={() => setRejecting(false)} disabled={working} className={quiet}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}