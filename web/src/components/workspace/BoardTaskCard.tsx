"use client";

import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { GitGrantPanel } from "@/components/workspace/GitGrantPanel";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import type { TaskRow } from "@/lib/api/workspace";
import { cn } from "@/lib/utils";

const RESUMABLE = new Set(["open", "claimed", "blocked", "failed"]);
const DELIVERY_PHASES = [
  { key: "build", label: "Build" },
  { key: "test", label: "Test" },
  { key: "verify", label: "Verify" },
  { key: "close", label: "Close" },
] as const;

export type TaskAction = "resume" | "restart" | "approve";

function attentionReason(task: TaskRow): string {
  const reason = task.attention?.trim();
  return reason ? reason : "reason unavailable";
}

function formatWhen(iso: string): string {
  if (!iso) return "unknown";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleString();
}

function formatRelative(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const sec = Math.max(0, Math.round((nowMs - ms) / 1000));
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 48) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

/** Plain-language status from live cursor + ledger flags — never invents progress. */
export function deliveryStatus(task: TaskRow): string | null {
  if (task.clone_failed) return `Clone failed: ${task.clone_failed}`;
  if (task.waiting_git_grant) return "Waiting for GitHub access grant";
  if (task.needs_spec) return "Needs GitHub repo before delivery";
  if (task.sealing) return "Sealing hidden checks…";
  if (task.state === "done") return "Delivery complete";
  if (task.state === "needs_review") {
    if (task.head_verdict === "pass") return "Passed verify — awaiting your approval";
    if (task.head_verdict) return `Review verdict: ${task.head_verdict}`;
    return "Needs your review";
  }
  if (task.state === "claimed" || task.state === "open") {
    const p = task.pipeline;
    if (p?.review === "attention") return "Rework — reviewer sent feedback";
    if (p?.build === "attention") return "Build needs attention";
    const resume = task.resume_at || null;
    const cursor = task.cursor_phase || null;
    if (resume === "verify" || cursor === "test_passed") return "Waiting on reviewer";
    if (resume === "close" || cursor === "verify_approved") return "Closing delivery…";
    if (resume === "test" || cursor === "build_done") return "Running tests…";
    if (task.state === "claimed") {
      return task.pr_url ? "Worker coding (PR open)…" : "Worker coding…";
    }
    if (task.state === "open" && p?.contract === "done") {
      return "Queued — ready to start delivery";
    }
    return "Queued";
  }
  if (task.state === "blocked" || task.state === "failed") {
    const base = task.attention?.trim() || null;
    if (task.resume_at && task.resume_at !== "build") {
      return base
        ? `${base} · resume at ${task.resume_at}`
        : `Resume at ${task.resume_at}`;
    }
    if (task.failure_class) {
      return base ? `${base} · ${task.failure_class}` : `Failure: ${task.failure_class}`;
    }
    return base || (task.state === "blocked" ? "Blocked" : "Failed");
  }
  if (task.pr_url) return "Pull request ready";
  return null;
}

function deliveryPhaseIndex(task: TaskRow): number {
  if (task.state === "done") return 4;
  const resume = task.resume_at || null;
  const cursor = task.cursor_phase || null;
  if (resume === "close" || cursor === "verify_approved" || cursor === "closed") return 3;
  if (resume === "verify" || cursor === "test_passed") return 2;
  if (resume === "test" || cursor === "build_done") return 1;
  return 0;
}

function statusTone(task: TaskRow, statusLine: string | null): string {
  if (task.clone_failed || task.state === "failed" || task.state === "blocked") {
    return "text-danger";
  }
  if (task.waiting_git_grant || task.state === "needs_review") return "text-warning";
  if (task.state === "done") return "text-success";
  if (statusLine?.includes("Waiting on reviewer") || statusLine?.includes("Running tests")) {
    return "text-cloud";
  }
  return "text-cloud";
}

function DeliveryPhaseStrip({ task }: { task: TaskRow }) {
  const idx = deliveryPhaseIndex(task);
  return (
    <ol
      data-delivery-phase
      className="mt-2 flex flex-wrap items-center gap-1 font-mono text-[10px] uppercase tracking-wider"
      aria-label="delivery phase"
    >
      {DELIVERY_PHASES.map((step, i) => {
        const done = i < idx || task.state === "done";
        const current = i === idx && task.state !== "done";
        return (
          <li key={step.key} className="flex items-center gap-1">
            {i > 0 ? <span className="text-dim/50">·</span> : null}
            <span
              className={cn(
                "rounded px-1.5 py-0.5",
                done && "bg-success/15 text-success",
                current && "bg-brand/15 text-brand-300",
                !done && !current && "text-dim",
              )}
              data-phase={step.key}
              data-phase-state={done ? "done" : current ? "current" : "pending"}
            >
              {step.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function BoardTaskCard({
  task,
  showAttention,
  nowMs,
  actionBusy,
  actionMsg,
  onAction,
}: {
  task: TaskRow;
  showAttention: boolean;
  /** Board poll clock — keeps relative times fresh without timers. */
  nowMs: number;
  actionBusy?: TaskAction | null;
  actionMsg?: string;
  onAction?: (task: TaskRow, action: TaskAction) => void;
}) {
  const statusLine = deliveryStatus(task);
  const canResume = RESUMABLE.has(task.state) && !!onAction;
  const canRestart = canResume;
  const canApprove =
    task.state === "needs_review" &&
    task.head_verdict === "pass" &&
    !!onAction;
  const phase = task.resume_at || "build";
  const resumeLabel = phase === "build" ? "Resume" : `Resume at ${phase}`;
  const relative = formatRelative(task.updated_at, nowMs);
  const busy = Boolean(actionBusy);
  const attn = attentionReason(task);

  return (
    <li data-board-task={task.id} data-board-task-state={task.state}>
      <div className="rounded-2xl border border-steel/30 bg-navy/60 px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <Link
            href={`/workspace/tasks/${task.id}`}
            className="min-w-0 flex-1"
          >
            <h3 className="text-base font-semibold text-white">{task.title}</h3>
          </Link>
          <TaskStatePill state={task.state} />
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11px] text-muted">
          <span>#{task.id}</span>
          <span aria-hidden>·</span>
          <span>
            updated{" "}
            <time dateTime={task.updated_at} title={formatWhen(task.updated_at)}>
              {relative || formatWhen(task.updated_at)}
            </time>
          </span>
          {task.tenant && task.project ? (
            <>
              <span aria-hidden>·</span>
              <span className="truncate">
                {task.tenant}/{task.project}
              </span>
            </>
          ) : null}
          {task.failure_class ? (
            <>
              <span aria-hidden>·</span>
              <span data-failure-class className="text-danger">
                {task.failure_class}
              </span>
            </>
          ) : null}
        </div>

        {statusLine ? (
          <p
            data-task-status
            className={cn("mt-2 text-sm font-medium", statusTone(task, statusLine))}
          >
            {statusLine}
          </p>
        ) : null}

        <DeliveryPhaseStrip task={task} />

        {task.pipeline ? (
          <div className="mt-2">
            <PipelineStrip pipeline={task.pipeline} variant="mini" />
          </div>
        ) : null}

        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-dim">
          {task.needs_spec ? <span data-spec-badge>Needs GitHub repo</span> : null}
          {task.sealing ? <span data-seal-badge>Sealing hidden checks…</span> : null}
          {task.head_verdict ? (
            <span data-head-verdict>Verify: {task.head_verdict}</span>
          ) : null}
          {task.cursor_phase ? (
            <span data-cursor-phase>Checkpoint: {task.cursor_phase}</span>
          ) : null}
          {showAttention && statusLine && !statusLine.includes(attn) ? (
            <span className="truncate text-muted">{attn}</span>
          ) : null}
        </div>

        {actionMsg ? (
          <p className="mt-2 font-mono text-[11px] text-dim" role="status">
            {actionMsg}
          </p>
        ) : null}

        {task.waiting_git_grant ? (
          <div className="mt-2" data-board-git-grant>
            <GitGrantPanel taskId={task.id} />
          </div>
        ) : null}

        <div
          data-task-actions
          className="mt-3 flex flex-wrap items-center gap-2 border-t border-steel/20 pt-3"
        >
          {canResume ? (
            <Button
              type="button"
              variant="secondary"
              data-task-resume={task.id}
              disabled={busy}
              onClick={() => onAction?.(task, "resume")}
              className="h-7 px-3 text-[11px]"
            >
              {actionBusy === "resume" ? "Resuming…" : resumeLabel}
            </Button>
          ) : null}
          {canRestart ? (
            <Button
              type="button"
              variant="ghost"
              data-task-restart={task.id}
              disabled={busy}
              onClick={() => onAction?.(task, "restart")}
              className="h-7 px-3 text-[11px]"
            >
              {actionBusy === "restart" ? "Restarting…" : "Restart from build"}
            </Button>
          ) : null}
          {canApprove ? (
            <Button
              type="button"
              data-task-approve={task.id}
              disabled={busy}
              onClick={() => onAction?.(task, "approve")}
              className="h-7 px-3 text-[11px]"
            >
              {actionBusy === "approve" ? "Approving…" : "Approve"}
            </Button>
          ) : null}
          {task.pr_url ? (
            <a
              href={task.pr_url}
              target="_blank"
              rel="noreferrer"
              data-task-pr
              className="inline-flex h-7 items-center px-1 font-mono text-[11px] text-brand-300 hover:underline"
            >
              Open PR ↗
            </a>
          ) : null}
          <Link
            href={`/workspace/tasks/${task.id}`}
            className="inline-flex h-7 items-center px-1 font-mono text-[11px] text-muted hover:text-fog"
          >
            Details →
          </Link>
        </div>
      </div>
    </li>
  );
}
