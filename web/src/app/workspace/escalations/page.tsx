"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/workspace/session";
import { csrfHeaders, ws, type TaskRow } from "@/lib/api/workspace";
import {
  ESCALATION_STATES,
  ESCALATION_TRANSITIONS,
  REQUEST_CHANGES_KIND,
  buildRequestChangesBody,
  escalationActionsForState,
  grantExpiryIso,
  grantScopeForTask,
} from "@/lib/escalations";

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

interface MoveRecord {
  taskId: number;
  title: string;
  from: string;
  to: string;
  at: string;
}

export default function EscalationsPage() {
  const router = useRouter();
  const { tenant } = useSession();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [feedbacks, setFeedbacks] = useState<Record<number, string>>({});
  const [approvers, setApprovers] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<Record<number, string | null>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [grantStatus, setGrantStatus] = useState<Record<number, string>>({});
  const [moves, setMoves] = useState<MoveRecord[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const { data, response } = await ws.GET("/api/tasks");
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        setError(`escalations failed (${response.status})`);
        return;
      }
      setTasks(data.items.filter((t) => ESCALATION_STATES.has(t.state)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "escalations failed");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    // Mount fetch: single-shot inbox load, not a render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tasks;
    return tasks.filter(
      (t) => t.title.toLowerCase().includes(q) || String(t.id).includes(q),
    );
  }, [tasks, query]);

  const counts = useMemo(() => {
    let review = 0;
    let stuck = 0;
    for (const t of tasks) {
      if (t.state === "needs_review") review += 1;
      else if (t.state === "blocked" || t.state === "failed") stuck += 1;
    }
    return { review, stuck, total: tasks.length };
  }, [tasks]);

  function setRowBusy(id: number, action: string | null) {
    setBusy((prev) => ({ ...prev, [id]: action }));
  }

  function setRowError(id: number, message: string) {
    setRowErrors((prev) => ({ ...prev, [id]: message }));
  }

  function clearRowError(id: number) {
    setRowErrors((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  async function postTransition(id: number, to: string): Promise<boolean> {
    const { response } = await ws.POST("/api/tasks/{id}/transition", {
      params: { path: { id: String(id) } },
      body: { to },
      headers: csrfHeaders(),
    });
    if (response.status === 401) {
      router.push("/workspace/login");
      return false;
    }
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      setRowError(id, detail || `transition failed (${response.status})`);
      return false;
    }
    return true;
  }

  function recordMove(task: TaskRow, to: string) {
    setMoves((prev) =>
      [
        {
          taskId: task.id,
          title: task.title,
          from: task.state,
          to,
          at: new Date().toLocaleString(),
        },
        ...prev,
      ].slice(0, 8),
    );
  }

  async function approve(task: TaskRow) {
    clearRowError(task.id);
    setRowBusy(task.id, "approve");
    try {
      const to = ESCALATION_TRANSITIONS.approve;
      if (!escalationActionsForState(task.state).includes("approve")) {
        setRowError(task.id, `approve needs needs_review (task is ${task.state})`);
        return;
      }
      if (await postTransition(task.id, to)) {
        recordMove(task, to);
        await load();
      }
    } catch (err) {
      setRowError(task.id, err instanceof Error ? err.message : "approve failed");
    } finally {
      setRowBusy(task.id, null);
    }
  }

  async function retry(task: TaskRow) {
    clearRowError(task.id);
    setRowBusy(task.id, "retry");
    try {
      const to = ESCALATION_TRANSITIONS.retry;
      if (!escalationActionsForState(task.state).includes("retry")) {
        setRowError(task.id, `retry needs blocked or failed (task is ${task.state})`);
        return;
      }
      if (await postTransition(task.id, to)) {
        recordMove(task, to);
        await load();
      }
    } catch (err) {
      setRowError(task.id, err instanceof Error ? err.message : "retry failed");
    } finally {
      setRowBusy(task.id, null);
    }
  }

  async function checkGrant(task: TaskRow) {
    clearRowError(task.id);
    setRowBusy(task.id, "grant-check");
    try {
      const { data, response } = await ws.GET("/api/grants/active", {
        params: { query: { scope: grantScopeForTask(task.id) } },
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        setRowError(task.id, `grant lookup failed (${response.status})`);
        return;
      }
      setGrantStatus((prev) => ({
        ...prev,
        [task.id]: data.active
          ? `grant active · expires ${formatWhen(data.grant?.expires_at ?? "")}`
          : "no active grant",
      }));
    } catch (err) {
      setRowError(task.id, err instanceof Error ? err.message : "grant lookup failed");
    } finally {
      setRowBusy(task.id, null);
    }
  }

  async function grant(task: TaskRow) {
    clearRowError(task.id);
    setRowBusy(task.id, "grant");
    try {
      if (!escalationActionsForState(task.state).includes("grant")) {
        setRowError(task.id, `grant needs blocked or failed (task is ${task.state})`);
        return;
      }
      const approvedBy = (approvers[task.id] ?? tenant ?? "owner").trim() || "owner";
      const scope = grantScopeForTask(task.id);
      const { response: grantRes } = await ws.POST("/api/grants", {
        body: { scope, expires_at: grantExpiryIso(7), approved_by: approvedBy },
        headers: csrfHeaders(),
      });
      if (grantRes.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!grantRes.ok) {
        const detail = await grantRes.text().catch(() => "");
        setRowError(task.id, detail || `grant failed (${grantRes.status})`);
        return;
      }
      // Grant recorded: note it, then reopen so the task moves observably.
      const { response: evRes } = await ws.POST("/api/tasks/{id}/evidence", {
        params: { path: { id: String(task.id) } },
        body: {
          kind: "escalation_resolved",
          body: { scope, approved_by: approvedBy, resolution: "grant" },
        },
        headers: csrfHeaders(),
      });
      if (!evRes.ok && evRes.status !== 401) {
        const detail = await evRes.text().catch(() => "");
        setRowError(task.id, detail || `grant note failed (${evRes.status})`);
        return;
      }
      if (evRes.status === 401) {
        router.push("/workspace/login");
        return;
      }
      const to = ESCALATION_TRANSITIONS.grant;
      if (await postTransition(task.id, to)) {
        recordMove(task, to);
        setGrantStatus((prev) => ({ ...prev, [task.id]: `grant active · scope ${scope}` }));
        await load();
      }
    } catch (err) {
      setRowError(task.id, err instanceof Error ? err.message : "grant failed");
    } finally {
      setRowBusy(task.id, null);
    }
  }

  async function requestChanges(task: TaskRow) {
    clearRowError(task.id);
    const feedback = (feedbacks[task.id] ?? "").trim();
    if (!feedback) {
      setRowError(task.id, "describe the requested change first");
      return;
    }
    setRowBusy(task.id, "request_changes");
    try {
      if (!escalationActionsForState(task.state).includes("request_changes")) {
        setRowError(task.id, `request changes needs needs_review (task is ${task.state})`);
        return;
      }
      // Supervisor-reject path: feedback first as a review-note evidence
      // entry, then needs_review -> claimed. The rework loop picks the task
      // up with the note verbatim in the coder's next prompt. Done is
      // terminal in the ledger, so rejection only applies pre-close.
      const { response: evRes } = await ws.POST("/api/tasks/{id}/evidence", {
        params: { path: { id: String(task.id) } },
        body: { kind: REQUEST_CHANGES_KIND, body: buildRequestChangesBody(feedback) },
        headers: csrfHeaders(),
      });
      if (evRes.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!evRes.ok) {
        const detail = await evRes.text().catch(() => "");
        setRowError(task.id, detail || `review note failed (${evRes.status})`);
        return;
      }
      const to = ESCALATION_TRANSITIONS.request_changes;
      if (await postTransition(task.id, to)) {
        recordMove(task, to);
        setFeedbacks((prev) => ({ ...prev, [task.id]: "" }));
        await load();
      }
    } catch (err) {
      setRowError(task.id, err instanceof Error ? err.message : "request changes failed");
    } finally {
      setRowBusy(task.id, null);
    }
  }

  return (
    <div data-escalations-inbox>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-white">Escalations inbox</h1>
        <p className="mt-1 text-sm text-muted">
          Approve, retry, grant, or request changes — every action moves a task.
        </p>
      </div>

      <dl className="mb-4 flex flex-wrap gap-2" aria-label="Escalation counts by state">
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">Needs review</dt>
          <dd className="font-mono text-sm text-cloud">{counts.review}</dd>
        </div>
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">
            Blocked / failed
          </dt>
          <dd className="font-mono text-sm text-cloud">{counts.stuck}</dd>
        </div>
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">Total</dt>
          <dd className="font-mono text-sm text-cloud">{counts.total}</dd>
        </div>
      </dl>

      <div className="mb-6">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search titles or ids…"
          aria-label="Search escalations by title or id"
          className="w-full min-w-0 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
        />
      </div>

      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {loading ? (
        <p className="font-mono text-sm text-muted">loading…</p>
      ) : visible.length === 0 && !error ? (
        <p className="rounded-2xl border border-steel/40 bg-abyss/60 p-6 text-center text-sm text-muted">
          Inbox empty — nothing needs review, blocked, or failed right now.
        </p>
      ) : (
        <ul className="space-y-3">
          {visible.map((task) => {
            const actions = escalationActionsForState(task.state);
            const isBusy = busy[task.id] != null;
            return (
              <li
                key={task.id}
                data-escalation-row={task.id}
                className="rounded-2xl border border-steel/30 bg-navy/60 px-4 py-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <Link
                    href={`/workspace/tasks/${task.id}`}
                    className="min-w-0 flex-1 text-base font-semibold text-white hover:underline"
                  >
                    {task.title}
                  </Link>
                  <span className="shrink-0">
                    <TaskStatePill state={task.state} />
                  </span>
                </div>
                <p className="mt-1 font-mono text-[11px] text-muted">
                  #{task.id} · updated{" "}
                  <time dateTime={task.updated_at}>{formatWhen(task.updated_at)}</time>
                </p>
                {task.pipeline ? (
                  <div className="mt-2">
                    <PipelineStrip pipeline={task.pipeline} variant="mini" />
                  </div>
                ) : null}
                <p className="mt-1 truncate text-xs text-muted">{attentionReason(task)}</p>

                <div className="mt-3 flex flex-wrap gap-2">
                  {actions.includes("approve") ? (
                    <Button
                      variant="primary"
                      disabled={isBusy}
                      data-action="approve"
                      onClick={() => void approve(task)}
                    >
                      {busy[task.id] === "approve" ? "Working…" : "Approve → done"}
                    </Button>
                  ) : null}
                  {actions.includes("retry") ? (
                    <Button
                      variant="secondary"
                      disabled={isBusy}
                      data-action="retry"
                      onClick={() => void retry(task)}
                    >
                      {busy[task.id] === "retry" ? "Working…" : "Retry → open"}
                    </Button>
                  ) : null}
                  {actions.includes("grant") ? (
                    <>
                      <input
                        value={approvers[task.id] ?? tenant ?? "owner"}
                        onChange={(e) =>
                          setApprovers((prev) => ({ ...prev, [task.id]: e.target.value }))
                        }
                        aria-label={`Approved by for task ${task.id}`}
                        placeholder="approved by"
                        className="w-32 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                      />
                      <Button
                        variant="secondary"
                        disabled={isBusy}
                        data-action="grant"
                        onClick={() => void grant(task)}
                      >
                        {busy[task.id] === "grant" ? "Working…" : "Grant + reopen"}
                      </Button>
                      <button
                        type="button"
                        disabled={isBusy}
                        data-action="grant-check"
                        onClick={() => void checkGrant(task)}
                        className="rounded-full px-4 py-2 font-mono text-xs text-muted transition-colors hover:text-cloud disabled:opacity-60"
                      >
                        {busy[task.id] === "grant-check" ? "Checking…" : "Check grant"}
                      </button>
                    </>
                  ) : null}
                </div>

                {actions.includes("request_changes") ? (
                  <div className="mt-3 space-y-2">
                    <label
                      htmlFor={`feedback-${task.id}`}
                      className="font-mono text-[11px] uppercase tracking-wider text-muted"
                    >
                      Request changes
                    </label>
                    <textarea
                      id={`feedback-${task.id}`}
                      value={feedbacks[task.id] ?? ""}
                      onChange={(e) =>
                        setFeedbacks((prev) => ({ ...prev, [task.id]: e.target.value }))
                      }
                      placeholder="What must change before approval…"
                      aria-label={`Requested change for task ${task.id}`}
                      data-feedback-input={task.id}
                      rows={2}
                      className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                    />
                    <div>
                      <Button
                        variant="secondary"
                        disabled={isBusy}
                        data-action="request_changes"
                        onClick={() => void requestChanges(task)}
                      >
                        {busy[task.id] === "request_changes"
                          ? "Working…"
                          : "Request changes → claimed"}
                      </Button>
                    </div>
                    <p className="font-mono text-[11px] text-muted/60">
                      Posts a review note, then moves needs_review → claimed. The note
                      returns verbatim in the coder&apos;s next prompt.
                    </p>
                  </div>
                ) : null}

                {grantStatus[task.id] ? (
                  <p data-grant-status={task.id} className="mt-2 font-mono text-xs text-muted">
                    {grantStatus[task.id]}
                  </p>
                ) : null}
                {rowErrors[task.id] ? (
                  <p className="mt-2 text-sm text-danger">{rowErrors[task.id]}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {moves.length > 0 ? (
        <section aria-label="Moved this session" className="mt-8">
          <h2 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
            Moved this session
          </h2>
          <ul className="space-y-1.5">
            {moves.map((m) => (
              <li
                key={`${m.taskId}-${m.at}`}
                data-move-note={m.taskId}
                className="rounded-lg border border-steel/30 bg-abyss/60 px-3 py-2 font-mono text-xs text-cloud"
              >
                <span className="text-muted">#{m.taskId}</span> {m.title}{" "}
                <span className="text-muted">
                  {m.from} → {m.to} · {m.at}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
