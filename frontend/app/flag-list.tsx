import type { Flag } from "@/lib/api";

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

export default function FlagList({ flags, unplaced }: { flags: Flag[]; unplaced: Flag[] }) {
  if (flags.length === 0) {
    return (
      <p className="rounded border border-green-600 p-4 text-green-600">
        No problems found. The invoice adds up and nothing is missing.
      </p>
    );
  }

  const errors = flags.filter((flag) => flag.severity === "error").length;
  const warnings = flags.length - errors;
  const summary = [
    errors > 0 ? plural(errors, "error") : null,
    warnings > 0 ? plural(warnings, "warning") : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <section aria-label="Checks" className="transition-opacity group-data-[checking=true]:opacity-60">
      <h2 className="text-xl font-semibold">Checks: {summary}</h2>
      {flags.length > unplaced.length && (
        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
          Each problem is marked next to the field it refers to.
        </p>
      )}
      {unplaced.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {unplaced.map((flag, index) => (
            <li
              key={`${flag.code}-${flag.field}-${index}`}
              className={
                flag.severity === "error"
                  ? "rounded border border-red-500 p-3 text-red-600"
                  : "rounded border border-amber-500 p-3 text-amber-600"
              }
            >
              <span className="font-semibold">
                {flag.severity === "error" ? "Error" : "Warning"}:
              </span>{" "}
              {flag.message}
              <span className="mt-1 block font-mono text-xs opacity-70">{flag.field}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}