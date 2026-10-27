import { cn } from "@/lib/utils";

export type Status = "queued" | "running" | "passed" | "failed" | "review";

const styles: Record<Status, { label: string; cls: string; dot: string }> = {
  queued: { label: "Queued", cls: "border-steel text-muted", dot: "bg-steel-400" },
  running: {
    label: "Running",
    cls: "border-brand/40 text-brand-300 bg-brand/10",
    dot: "bg-brand animate-pulse-dot",
  },
  passed: {
    label: "Passed",
    cls: "border-success/35 text-success bg-success/10",
    dot: "bg-success",
  },
  failed: {
    label: "Failed",
    cls: "border-danger/35 text-danger bg-danger/10",
    dot: "bg-danger",
  },
  review: {
    label: "Needs review",
    cls: "border-warning/35 text-warning bg-warning/10",
    dot: "bg-warning",
  },
};

export function StatusPill({
  status,
  label,
  className,
}: {
  status: Status;
  label?: string;
  className?: string;
}) {
  const s = styles[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        s.cls,
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", s.dot)} aria-hidden />
      {label ?? s.label}
    </span>
  );
}
