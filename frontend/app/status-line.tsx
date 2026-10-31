import type { DocumentStatus } from "@/lib/api";
import { statusHint, statusLabel } from "@/lib/status";

const TONE: Record<DocumentStatus, string> = {
  pending: "border-zinc-400 dark:border-zinc-600",
  approved: "border-green-600 text-green-600",
  rejected: "border-red-500 text-red-600",
};

export default function StatusLine({ status }: { status: DocumentStatus }) {
  const hint = statusHint(status);
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <span className="font-medium">Status</span>
      <span
        className={`rounded-full border px-3 py-0.5 font-medium ${TONE[status] ?? TONE.pending}`}
      >
        {statusLabel(status)}
      </span>
      {hint && <span className="text-zinc-600 dark:text-zinc-400">{hint}</span>}
    </p>
  );
}