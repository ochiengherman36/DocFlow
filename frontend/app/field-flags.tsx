import type { Flag } from "@/lib/api";

export default function FieldFlags({
  id,
  flags,
  align = "left",
  label,
}: {
  id: string;
  flags: Flag[] | undefined;
  align?: "left" | "right";
  label?: (flag: Flag) => string | null;
}) {
  if (!flags || flags.length === 0) return null;

  return (
    <ul
      id={id}
      className={`flex flex-col gap-0.5 text-sm transition-opacity group-data-[checking=true]:opacity-60 ${
        align === "right" ? "text-right" : ""
      }`}
    >
      {flags.map((flag, index) => {
        const lead = label?.(flag);
        return (
          <li
            key={`${flag.code}-${index}`}
            className={flag.severity === "error" ? "text-red-600" : "text-amber-600"}
          >
            <span className="font-semibold">{flag.severity === "error" ? "Error" : "Warning"}:</span>{" "}
            {lead ? `${lead}: ` : ""}
            {flag.message}
          </li>
        );
      })}
    </ul>
  );
}