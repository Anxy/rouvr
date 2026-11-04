"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown } from "lucide-react";
import { Combobox } from "@/components/ui/Select";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/workspace/session";
import { parseSseFrames, ws, type TaskRow } from "@/lib/api/workspace";
import { cn } from "@/lib/utils";

const NEEDS_ATTENTION = new Set(["blocked", "failed", "needs_review"]);
const IN_PROGRESS = new Set(["open", "claimed"]);

const PAGE_SIZE = 10;
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

// One client-side pager per list: 10 rows per page with prev/next and a
// single "Showing x–y of n" line per section (metadata rendered once per
// section, never repeated per row).
function Pager({
  page,
  total,
  onPage,
  label,
}: {
  page: number;
  total: number;
  onPage: (page: number) => void;
  label: string;
}) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (totalPages <= 1) return null;
  const safePage = Math.min(Math.max(1, page), totalPages);
  const start = (safePage - 1) * PAGE_SIZE + 1;
  const end = Math.min(safePage * PAGE_SIZE, total);
  return (
    <div className="mt-2 flex items-center gap-2" role="group" aria-label={label}>
      <button
        type="button"
        onClick={() => onPage(safePage - 1)}
        disabled={safePage <= 1}
        aria-label={`${label}: previous page`}
        className="rounded-lg border border-steel/40 px-2.5 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud disabled:opacity-40 disabled:hover:border-steel/40 disabled:hover:text-muted"
      >
        Prev
      </button>
      <span className="font-mono text-[11px] text-muted">
        Showing {start}–{end} of {total}
      </span>
      <button
        type="button"
        onClick={() => onPage(safePage + 1)}
        disabled={safePage >= totalPages}
        aria-label={`${label}: next page`}
        className="rounded-lg border border-steel/40 px-2.5 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud disabled:opacity-40 disabled:hover:border-steel/40 disabled:hover:text-muted"
      >
        Next
      </button>
    </div>
  );
}

// Collapsible board section: the header toggle carries the count + scope
// metadata once; the list scrolls internally so long queues stay bounded.
function BoardSection({
  id,
  title,
  count,
  scope,
  collapsed,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  count: number;
  scope: string | null;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section aria-label={title}>
      <header className="mb-2 flex items-baseline justify-between gap-3">
        <h2 className="font-mono text-xs uppercase tracking-wider text-muted">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-controls={id}
            title={collapsed ? `Expand ${title}` : `Collapse ${title}`}
            className="flex items-center gap-1.5 transition-colors hover:text-cloud"
          >
            <ChevronDown
              className={cn("size-3.5 transition-transform duration-200", collapsed && "-rotate-90")}
              aria-hidden
            />
            {title}
          </button>
        </h2>
        <span className="font-mono text-xs text-muted">
          {count}
          {scope ? ` · ${scope}` : ""}
        </span>
      </header>
      {collapsed ? null : children}
    </section>
  );
}

export default function BoardPage() {
  const router = useRouter();
  // Choosing a project here moves the whole workspace with it (header,
  // sidebar, chat); "All projects" stays a board-only view.
  const { projects, setProject: setSessionProject } = useSession();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [projectFilter, setProjectFilter] = useState("All");
  const [projectInput, setProjectInput] = useState("All projects");
  const [stateFilter, setStateFilter] = useState<
    "all" | "open" | "claimed" | "needs_review" | "stuck" | "done"
  >("all");
  const [sortDirection, setSortDirection] = useState<"newest" | "oldest">("newest");
  // Done starts collapsed; every section toggles open/closed from its header.
  const [collapsed, setCollapsed] = useState({
    attention: false,
    progress: false,
    done: true,
    activity: false,
  });
  // Client-side 10/page slices; page numbers clamp to the filtered length
  // so narrowing the filters can never strand a section on an empty page.
  const [pages, setPages] = useState({ attention: 1, progress: 1, done: 1, activity: 1 });
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
      if (stateFilter === "open" && t.state !== "open") return false;
      if (stateFilter === "claimed" && t.state !== "claimed") return false;
      if (stateFilter === "needs_review" && t.state !== "needs_review") return false;
      if (stateFilter === "stuck" && t.state !== "blocked" && t.state !== "failed")
        return false;
      if (stateFilter === "done" && t.state !== "done") return false;
      if (!q) return true;
      return (
        t.title.toLowerCase().includes(q) || String(t.id).includes(q)
      );
    });
  }, [tasks, query, projectFilter, stateFilter]);

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
  const pageOf = <T,>(list: T[], page: number): { rows: T[]; safePage: number } => {
    const totalPages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    const safePage = Math.min(Math.max(1, page), totalPages);
    return { rows: list.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE), safePage };
  };

  const attentionPage = pageOf(needsAttention, pages.attention);
  const progressPage = pageOf(inProgress, pages.progress);
  const donePage = pageOf(doneTasks, pages.done);
  const activityPage = pageOf(activity, pages.activity);

  const attentionScope = scopeLabel(needsAttention);
  const progressScope = scopeLabel(inProgress);
  const doneScope = scopeLabel(doneTasks);

  const projectDisplayOptions = useMemo(
    () => projectOptions.map((name) => (name === "All" ? "All projects" : name)),
    [projectOptions],
  );

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

      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filter tasks by state">
        {(
          [
            ["open", "Open", counts.open],
            ["claimed", "Claimed", counts.claimed],
            ["needs_review", "Needs review", counts.review],
            ["stuck", "Blocked / failed", counts.stuck],
            ["done", "Done", counts.done],
          ] as const
        ).map(([key, label, n]) => {
          const active = stateFilter === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setStateFilter(active ? "all" : key)}
              aria-pressed={active}
              title={active ? `Showing ${label} — click to clear` : `Show only ${label}`}
              className={`rounded-lg border px-3 py-1.5 text-left transition-colors ${
                active
                  ? "border-brand/70 bg-brand/10"
                  : "border-steel/40 bg-abyss/60 hover:border-brand/50"
              }`}
            >
              <span className="block font-mono text-[10px] uppercase tracking-wider text-muted">
                {label}
              </span>
              <span className="block font-mono text-sm text-cloud">{n}</span>
            </button>
          );
        })}
        <div className="rounded-lg border border-steel/40 bg-abyss/60 px-3 py-1.5">
          <span className="block font-mono text-[10px] uppercase tracking-wider text-muted">
            Last activity
          </span>
          <span
            className="block font-mono text-sm text-cloud"
            title="Newest updated_at across the listed tasks — the list rows carry no per-task review timestamps, so this is last activity, not last review."
          >
            {lastActivity}
          </span>
        </div>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search titles or ids…"
          aria-label="Search tasks by title or id"
          className="min-w-0 flex-1 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
        />
        <Combobox
          id="board-project-filter"
          value={projectInput}
          onChange={(v) => {
            setProjectInput(v);
            if (v === "" || v === "All projects") setProjectFilter("All");
            else if (projectOptions.includes(v)) {
              setProjectFilter(v);
              setSessionProject(v);
            }
          }}
          onBlur={() => {
            if (!projectOptions.includes(projectInput) && projectInput !== "All projects") {
              setProjectInput("All projects");
              setProjectFilter("All");
            }
          }}
          options={projectDisplayOptions}
          ariaLabel="Filter by project — type to filter"
          placeholder="All projects"
        />
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
          <BoardSection
            id="board-needs-attention"
            title="Needs attention"
            count={needsAttention.length}
            scope={attentionScope}
            collapsed={collapsed.attention}
            onToggle={() => setCollapsed((c) => ({ ...c, attention: !c.attention }))}
          >
            {needsAttention.length === 0 ? (
              <p className="rounded-xl border border-steel/30 bg-abyss/60 px-3 py-4 text-center font-mono text-xs text-muted/60">
                empty
              </p>
            ) : (
              <div id="board-needs-attention">
                <ul className="max-h-[480px] space-y-1.5 overflow-y-auto pr-1">
                  {attentionPage.rows.map((t) => (
                    <TaskCard key={t.id} task={t} showAttention />
                  ))}
                </ul>
                <Pager
                  page={attentionPage.safePage}
                  total={needsAttention.length}
                  onPage={(p) => setPages((prev) => ({ ...prev, attention: p }))}
                  label="Needs attention pages"
                />
              </div>
            )}
          </BoardSection>

          <BoardSection
            id="board-in-progress"
            title="In progress"
            count={inProgress.length}
            scope={progressScope}
            collapsed={collapsed.progress}
            onToggle={() => setCollapsed((c) => ({ ...c, progress: !c.progress }))}
          >
            {inProgress.length === 0 ? (
              <p className="rounded-xl border border-steel/30 bg-abyss/60 px-3 py-4 text-center font-mono text-xs text-muted/60">
                empty
              </p>
            ) : (
              <div id="board-in-progress">
                <ul className="max-h-[480px] space-y-1.5 overflow-y-auto pr-1">
                  {progressPage.rows.map((t) => (
                    <TaskCard key={t.id} task={t} showAttention={false} />
                  ))}
                </ul>
                <Pager
                  page={progressPage.safePage}
                  total={inProgress.length}
                  onPage={(p) => setPages((prev) => ({ ...prev, progress: p }))}
                  label="In progress pages"
                />
              </div>
            )}
          </BoardSection>

          <BoardSection
            id="board-done"
            title="Done"
            count={doneTasks.length}
            scope={doneScope}
            collapsed={collapsed.done}
            onToggle={() => setCollapsed((c) => ({ ...c, done: !c.done }))}
          >
            {doneTasks.length === 0 ? (
              <p className="rounded-xl border border-steel/30 bg-abyss/60 px-3 py-4 text-center font-mono text-xs text-muted/60">
                empty
              </p>
            ) : (
              <div id="board-done">
                <ul className="max-h-[480px] space-y-1.5 overflow-y-auto pr-1">
                  {donePage.rows.map((t) => (
                    <TaskCard key={t.id} task={t} showAttention={false} />
                  ))}
                </ul>
                <Pager
                  page={donePage.safePage}
                  total={doneTasks.length}
                  onPage={(p) => setPages((prev) => ({ ...prev, done: p }))}
                  label="Done pages"
                />
              </div>
            )}
          </BoardSection>

          <BoardSection
            id="board-activity"
            title="Recent activity"
            count={activity.length}
            scope={null}
            collapsed={collapsed.activity}
            onToggle={() => setCollapsed((c) => ({ ...c, activity: !c.activity }))}
          >
            {activityError ? (
              <p className="font-mono text-xs text-muted/60">{activityError}</p>
            ) : activityLoading ? (
              <p className="font-mono text-xs text-muted/60">tailing…</p>
            ) : activity.length === 0 ? (
              <p className="font-mono text-xs text-muted/60">no events yet</p>
            ) : (
              <div id="board-activity">
                <ul className="max-h-[480px] space-y-1.5 overflow-y-auto pr-1">
                  {activityPage.rows.map((e) => (
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
                <Pager
                  page={activityPage.safePage}
                  total={activity.length}
                  onPage={(p) => setPages((prev) => ({ ...prev, activity: p }))}
                  label="Recent activity pages"
                />
              </div>
            )}
          </BoardSection>
        </div>
      )}
    </div>
  );
}
