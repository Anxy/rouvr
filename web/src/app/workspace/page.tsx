"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/workspace/session";
import { parseSseFrames, ws, type TaskRow } from "@/lib/api/workspace";

const NEEDS_ATTENTION = new Set(["blocked", "failed", "needs_review"]);
const IN_PROGRESS = new Set(["open", "claimed"]);

const DONE_PREVIEW_LIMIT = 10;
const ACTIVITY_LIMIT = 8;
// Board activity reads the same ledger outbox snapshot as the task page's
// live tail (GET /api/events -> /v1/events, SSE frames). The ledger replays
// oldest-first from a cursor, so the board pages forward from the start
// until a short page (or the page cap) and keeps the tail: the newest
// ACTIVITY_LIMIT envelopes by id. Nothing is synthesized — every row renders
// one real envelope (time + plain label).
const EVENTS_PAGE_LIMIT = 100;
const EVENTS_MAX_PAGES = 5;

// Board badges use the shared TaskStatePill colours (done green, open blue,
// needs_review amber, blocked/failed red); in-flight states fall back to the
// neutral pill tone.

function attentionReason(task: TaskRow): string {
  const reason = task.attention?.trim();
  return reason ? reason : "reason unavailable";
}

function byUpdatedAt(direction: "newest" | "oldest") {
  return (a: TaskRow, b: TaskRow) => {
    const da = Date.parse(a.updated_at);
    const db = Date.parse(b.updated_at);
    const sa = Number.isNaN(da) ? 0 : da;
    const sb = Number.isNaN(db) ? 0 : db;
    return direction === "newest" ? sb - sa : sa - sb;
  };
}

function formatWhen(iso: string): string {
  if (!iso) return "unknown";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleString();
}

// One compact scope label per section ("acme / demo", or "acme / demo +2
// more") so cards never repeat the same tenant / project string on every row.
function scopeLabel(tasks: TaskRow[]): string | null {
  if (tasks.length === 0) return null;
  const pairs = [...new Set(tasks.map((t) => `${t.tenant} / ${t.project}`))].sort();
  if (pairs.length === 1) return pairs[0];
  return `${pairs[0]} +${pairs.length - 1} more`;
}

interface BoardEvent {
  id: number;
  type: string;
  taskId: number | null;
  createdAt: string;
  label: string;
}

interface EventEnvelope {
  id: number;
  type: string;
  task_id: number | null;
  created_at: string;
  payload: unknown;
}

function eventLabel(env: EventEnvelope): string {
  const payload = (env.payload ?? {}) as {
    title?: unknown;
    from?: unknown;
    to?: unknown;
  };
  const title = typeof payload.title === "string" ? payload.title : "";
  const from = typeof payload.from === "string" ? payload.from : "";
  const to = typeof payload.to === "string" ? payload.to : "";
  const what = title || (from && to ? `${from} → ${to}` : env.type);
  const ref = env.task_id == null ? "ledger" : `task #${env.task_id}`;
  return `${ref} · ${env.type} · ${what}`;
}

function TaskCard({ task, showAttention }: { task: TaskRow; showAttention: boolean }) {
  return (
    <li>
      <Link
        href={`/workspace/tasks/${task.id}`}
        className="block rounded-2xl border border-steel/30 bg-navy/60 px-4 py-3 transition-colors hover:border-brand/50"
      >
        <div className="flex items-start justify-between gap-3">
          <h3 className="min-w-0 flex-1 text-base font-semibold text-white">
            {task.title}
          </h3>
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
        {task.sealing ? (
          <p data-seal-badge className="mt-1 font-mono text-[11px] text-muted">
            Sealing hidden checks…
          </p>
        ) : null}
        {showAttention ? (
          <p className="mt-1 truncate text-xs text-muted">{attentionReason(task)}</p>
        ) : null}
      </Link>
    </li>
  );
}

export default function BoardPage() {
  const router = useRouter();
  const { projects } = useSession();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [projectFilter, setProjectFilter] = useState("All");
  const [sortDirection, setSortDirection] = useState<"newest" | "oldest">("newest");
  const [showAllDone, setShowAllDone] = useState(false);
  const [activity, setActivity] = useState<BoardEvent[]>([]);
  const [activityError, setActivityError] = useState("");
  const [activityLoading, setActivityLoading] = useState(true);

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
        setError(`board failed (${response.status})`);
        return;
      }
      setTasks(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "board failed");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    // Mount fetch: single-shot board load, not a render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const loadActivity = useCallback(
    async (alive: { current: boolean }) => {
      setActivityLoading(true);
      setActivityError("");
      try {
        const seen: EventEnvelope[] = [];
        let cursor = "";
        for (let page = 0; page < EVENTS_MAX_PAGES; page += 1) {
          const { response } = await ws.GET("/api/events", {
            parseAs: "stream",
            params: {
              query: {
                live: "0",
                limit: String(EVENTS_PAGE_LIMIT),
                ...(cursor ? { cursor } : {}),
              },
            } as never,
          });
          if (!alive.current) return;
          if (response.status === 401) {
            router.push("/workspace/login");
            return;
          }
          if (!response.ok) {
            setActivityError(`activity unavailable (${response.status})`);
            return;
          }
          const text = await response.text();
          if (!alive.current) return;
          let count = 0;
          for (const frame of parseSseFrames(text)) {
            if (!frame.event || frame.event === "error") continue;
            try {
              const env = JSON.parse(frame.data) as EventEnvelope;
              if (typeof env.id !== "number") continue;
              seen.push(env);
              count += 1;
            } catch {
              /* skip malformed frames; the ledger only sends JSON envelopes */
            }
          }
          if (count < EVENTS_PAGE_LIMIT) break;
          const maxId = seen.reduce((m, e) => Math.max(m, e.id), 0);
          if (maxId <= 0) break;
          cursor = String(maxId);
        }
        seen.sort((a, b) => b.id - a.id);
        if (!alive.current) return;
        setActivity(
          seen.slice(0, ACTIVITY_LIMIT).map((env) => ({
            id: env.id,
            type: env.type,
            taskId: env.task_id,
            createdAt: env.created_at,
            label: eventLabel(env),
          })),
        );
      } catch (err) {
        if (alive.current) {
          setActivityError(err instanceof Error ? err.message : "activity unavailable");
        }
      } finally {
        if (alive.current) setActivityLoading(false);
      }
    },
    [router],
  );

  useEffect(() => {
    // Mount fetch: one closed backlog snapshot (live=0), not a live tail.
    const alive = { current: true };
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadActivity(alive);
    return () => {
      alive.current = false;
    };
  }, [loadActivity]);

  const projectOptions = useMemo(() => {
    const names = new Set<string>();
    for (const p of projects) names.add(p.project);
    for (const t of tasks) names.add(t.project);
    return ["All", ...[...names].sort()];
  }, [projects, tasks]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks.filter((t) => {
      if (projectFilter !== "All" && t.project !== projectFilter) return false;
      if (!q) return true;
      return (
        t.title.toLowerCase().includes(q) || String(t.id).includes(q)
      );
    });
  }, [tasks, query, projectFilter]);

  const counts = useMemo(() => {
    let open = 0;
    let claimed = 0;
    let review = 0;
    let stuck = 0;
    let done = 0;
    for (const t of visible) {
      if (t.state === "open") open += 1;
      else if (t.state === "claimed") claimed += 1;
      else if (t.state === "needs_review") review += 1;
      else if (t.state === "blocked" || t.state === "failed") stuck += 1;
      else if (t.state === "done") done += 1;
    }
    return { open, claimed, review, stuck, done };
  }, [visible]);

  // The task list rows carry no per-task review timestamps, and the board
  // must not fan out to per-task detail — so the summary card reports last
  // *activity* (newest updated_at across the listed tasks) and labels it
  // honestly instead of claiming a last-review time.
  const lastActivity = useMemo(() => {
    let raw = "";
    let latest = 0;
    for (const t of visible) {
      const ms = Date.parse(t.updated_at);
      if (!Number.isNaN(ms) && ms > latest) {
        latest = ms;
        raw = t.updated_at;
      }
    }
    return raw ? formatWhen(raw) : "—";
  }, [visible]);

  const needsAttention = useMemo(
    () =>
      visible
        .filter((t) => NEEDS_ATTENTION.has(t.state))
        .sort(byUpdatedAt(sortDirection)),
    [visible, sortDirection],
  );
  const inProgress = useMemo(
    () =>
      visible
        .filter((t) => IN_PROGRESS.has(t.state))
        .sort(byUpdatedAt(sortDirection)),
    [visible, sortDirection],
  );
  const doneTasks = useMemo(
    () =>
      visible.filter((t) => t.state === "done").sort(byUpdatedAt("newest")),
    [visible],
  );
  const doneShown = showAllDone ? doneTasks : doneTasks.slice(0, DONE_PREVIEW_LIMIT);

  const attentionScope = scopeLabel(needsAttention);
  const progressScope = scopeLabel(inProgress);
  const doneScope = scopeLabel(doneTasks);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">Project board</h1>
          <p className="mt-1 text-sm text-muted">
            What needs you first, what is moving, and what is done — live from the
            ledger.
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          <Button href="/workspace/chat" variant="secondary">
            New slice
          </Button>
        </div>
      </div>

      <dl className="mb-4 flex flex-wrap gap-2" aria-label="Task counts by state">
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">Open</dt>
          <dd className="font-mono text-sm text-cloud">{counts.open}</dd>
        </div>
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">Claimed</dt>
          <dd className="font-mono text-sm text-cloud">{counts.claimed}</dd>
        </div>
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
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">Done</dt>
          <dd className="font-mono text-sm text-cloud">{counts.done}</dd>
        </div>
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <dt className="font-mono text-[10px] uppercase tracking-wider text-muted">
            Last activity
          </dt>
          <dd
            className="font-mono text-sm text-cloud"
            title="Newest updated_at across the listed tasks — the list rows carry no per-task review timestamps, so this is last activity, not last review."
          >
            {lastActivity}
          </dd>
        </div>
      </dl>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search titles or ids…"
          aria-label="Search tasks by title or id"
          className="min-w-0 flex-1 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
        />
        <select
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)}
          aria-label="Filter by project"
          className="rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud focus:border-brand/60 focus:outline-none"
        >
          {projectOptions.map((name) => (
            <option key={name} value={name}>
              {name === "All" ? "All projects" : name}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() =>
            setSortDirection((d) => (d === "newest" ? "oldest" : "newest"))
          }
          aria-label={`Sort by updated time, currently ${sortDirection}`}
          className="rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 font-mono text-xs text-muted transition-colors hover:border-brand/50 hover:text-cloud"
        >
          {sortDirection === "newest" ? "Newest first" : "Oldest first"}
        </button>
      </div>

      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {loading ? (
        <p className="font-mono text-sm text-muted">loading…</p>
      ) : tasks.length === 0 && !error ? (
        <p className="rounded-2xl border border-steel/40 bg-abyss/60 p-6 text-center text-sm text-muted">
          No tasks yet — send a message in <Link href="/workspace/chat">Chat</Link> to create
          one.
        </p>
      ) : (
        <div className="space-y-6">
          <section aria-label="Needs attention">
            <header className="mb-2 flex items-baseline justify-between gap-3">
              <h2 className="font-mono text-xs uppercase tracking-wider text-muted">
                Needs attention
              </h2>
              <span className="font-mono text-xs text-muted">
                {needsAttention.length}
                {attentionScope ? ` · ${attentionScope}` : ""}
              </span>
            </header>
            {needsAttention.length === 0 ? (
              <p className="rounded-xl border border-steel/30 bg-abyss/60 px-3 py-4 text-center font-mono text-xs text-muted/60">
                empty
              </p>
            ) : (
              <ul className="space-y-1.5">
                {needsAttention.map((t) => (
                  <TaskCard key={t.id} task={t} showAttention />
                ))}
              </ul>
            )}
          </section>

          <section aria-label="In progress">
            <header className="mb-2 flex items-baseline justify-between gap-3">
              <h2 className="font-mono text-xs uppercase tracking-wider text-muted">
                In progress
              </h2>
              <span className="font-mono text-xs text-muted">
                {inProgress.length}
                {progressScope ? ` · ${progressScope}` : ""}
              </span>
            </header>
            {inProgress.length === 0 ? (
              <p className="rounded-xl border border-steel/30 bg-abyss/60 px-3 py-4 text-center font-mono text-xs text-muted/60">
                empty
              </p>
            ) : (
              <ul className="space-y-1.5">
                {inProgress.map((t) => (
                  <TaskCard key={t.id} task={t} showAttention={false} />
                ))}
              </ul>
            )}
          </section>

          <section aria-label="Done">
            <header className="mb-2 flex items-baseline justify-between gap-3">
              <h2 className="font-mono text-xs uppercase tracking-wider text-muted">Done</h2>
              <span className="font-mono text-xs text-muted">
                {doneTasks.length}
                {doneScope ? ` · ${doneScope}` : ""}
              </span>
            </header>
            {doneTasks.length === 0 ? (
              <p className="rounded-xl border border-steel/30 bg-abyss/60 px-3 py-4 text-center font-mono text-xs text-muted/60">
                empty
              </p>
            ) : (
              <>
                <ul className="space-y-1.5">
                  {doneShown.map((t) => (
                    <TaskCard key={t.id} task={t} showAttention={false} />
                  ))}
                </ul>
                {doneTasks.length > DONE_PREVIEW_LIMIT ? (
                  <button
                    type="button"
                    onClick={() => setShowAllDone((v) => !v)}
                    className="mt-2 font-mono text-xs text-muted transition-colors hover:text-cloud"
                  >
                    {showAllDone
                      ? "Show less"
                      : `Show more (${doneTasks.length - DONE_PREVIEW_LIMIT} more)`}
                  </button>
                ) : null}
              </>
            )}
          </section>

          <section aria-label="Recent activity">
            <header className="mb-2 flex items-baseline justify-between gap-3">
              <h2 className="font-mono text-xs uppercase tracking-wider text-muted">
                Recent activity
              </h2>
              <span className="font-mono text-xs text-muted">{activity.length}</span>
            </header>
            {activityError ? (
              <p className="font-mono text-xs text-muted/60">{activityError}</p>
            ) : activityLoading ? (
              <p className="font-mono text-xs text-muted/60">tailing…</p>
            ) : activity.length === 0 ? (
              <p className="font-mono text-xs text-muted/60">no events yet</p>
            ) : (
              <ul className="space-y-1.5">
                {activity.map((e) => (
                  <li
                    key={e.id}
                    className="rounded-lg border border-steel/30 bg-abyss/60 px-3 py-2 font-mono text-xs"
                  >
                    <time dateTime={e.createdAt} className="text-muted">
                      {formatWhen(e.createdAt)}
                    </time>{" "}
                    <span className="text-cloud">{e.label}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
