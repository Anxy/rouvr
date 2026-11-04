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

// Shared task-state colours (single source of truth for board + detail):
// done green, open blue, needs_review amber, blocked/failed red. Theme
// tokens only — no hard-coded hex — so dark/light themes stay in sync.
const TASK_STATE_STYLES: Record<string, { cls: string; dot: string }> = {
  done: { cls: "border-success/35 text-success bg-success/10", dot: "bg-success" },
  open: { cls: "border-brand/40 text-brand-300 bg-brand/10", dot: "bg-brand" },
  needs_review: { cls: "border-warning/35 text-warning bg-warning/10", dot: "bg-warning" },
  blocked: { cls: "border-danger/35 text-danger bg-danger/10", dot: "bg-danger" },
  failed: { cls: "border-danger/35 text-danger bg-danger/10", dot: "bg-danger" },
};

const TASK_STATE_FALLBACK = { cls: "border-steel text-muted", dot: "bg-steel-400" };

export function TaskStatePill({ state, className }: { state: string; className?: string }) {
  const s = TASK_STATE_STYLES[state] ?? TASK_STATE_FALLBACK;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        s.cls,
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", s.dot)} aria-hidden />
      {state}
    </span>
  );
}

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
