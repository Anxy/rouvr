"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { Combobox } from "@/components/ui/Select";
import { useSession } from "@/components/workspace/session";
import { csrfHeaders, ws, type TaskRow } from "@/lib/api/workspace";

// Tasks index: pick a project (type to filter), see its tasks like the
// board, and manage them — create with a contract, edit title/intent while
// open, or retire with a reason. The ledger enforces the rules (edit only
// open + unclaimed, retire never for done); refusal surfaces as an error.
function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}

export default function TasksPage() {
  const router = useRouter();
  const { projects, username } = useSession();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [projectFilter, setProjectFilter] = useState("All");
  const [projectInput, setProjectInput] = useState("All projects");
  const [query, setQuery] = useState("");

  const [showCreate, setShowCreate] = useState(false);
  const [cTitle, setCTitle] = useState("");
  const [cIntent, setCIntent] = useState("");
  const [cProject, setCProject] = useState("");
  const [cCriteria, setCCriteria] = useState("");
  const [cBusy, setCBusy] = useState(false);
  const [cError, setCError] = useState("");

  const [editingId, setEditingId] = useState<number | null>(null);
  const [eTitle, setETitle] = useState("");
  const [eIntent, setEIntent] = useState("");
  const [rowBusy, setRowBusy] = useState(false);
  const [rowError, setRowError] = useState("");
  const [retiringId, setRetiringId] = useState<number | null>(null);
  const [retireReason, setRetireReason] = useState("");

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
        setError(`tasks failed (${response.status})`);
        return;
      }
      setTasks(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "tasks failed");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    // Mount fetch: single-shot task list, not a render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

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
      return t.title.toLowerCase().includes(q) || String(t.id).includes(q);
    });
  }, [tasks, query, projectFilter]);

  async function createTask(e: React.FormEvent) {
    e.preventDefault();
    const criteria = cCriteria.split("\n").map((c) => c.trim()).filter(Boolean);
    if (!cTitle.trim() || criteria.length === 0) return;
    setCBusy(true);
    setCError("");
    try {
      const { data, response } = await ws.POST("/api/tasks", {
        body: {
          title: cTitle.trim(),
          intent: cIntent.trim() || undefined,
          project: cProject.trim() || undefined,
          acceptance_criteria: criteria,
        },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        setCError(`create failed (${response.status})`);
        return;
      }
      setCTitle("");
      setCIntent("");
      setCCriteria("");
      setShowCreate(false);
      await load();
      router.push(`/workspace/tasks/${data.id}`);
    } catch (err) {
      setCError(err instanceof Error ? err.message : "create failed");
    } finally {
      setCBusy(false);
    }
  }

  function startEdit(task: TaskRow) {
    setRowError("");
    setEditingId(task.id);
    setETitle(task.title);
    setEIntent("");
    setRetiringId(null);
  }

  async function saveEdit(id: number) {
    if (!eTitle.trim()) return;
    setRowBusy(true);
    setRowError("");
    try {
      const { response } = await ws.PATCH("/api/tasks/{id}", {
        params: { path: { id: String(id) } },
        body: {
          title: eTitle.trim(),
          intent: eIntent.trim() || undefined,
          idempotency_key: newKey(),
        },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response
          .json()
          .then((j) => (j as { detail?: string }).detail)
          .catch(() => undefined);
        setRowError(detail ?? `edit failed (${response.status})`);
        return;
      }
      setEditingId(null);
      await load();
    } catch (err) {
      setRowError(err instanceof Error ? err.message : "edit failed");
    } finally {
      setRowBusy(false);
    }
  }

  async function claimTask(id: number) {
    if (!username) {
      setRowError("sign in to claim this task");
      return;
    }
    setRowBusy(true);
    setRowError("");
    try {
      const { response } = await ws.POST("/api/tasks/{id}/claim", {
        params: { path: { id: String(id) } },
        body: { key: newKey(), principal: username },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setRowError(detail || `claim failed (${response.status})`);
        return;
      }
      await load();
    } catch (err) {
      setRowError(err instanceof Error ? err.message : "claim failed");
    } finally {
      setRowBusy(false);
    }
  }

  async function confirmRetire(id: number) {
    if (!retireReason.trim()) return;
    setRowBusy(true);
    setRowError("");
    try {
      const { response } = await ws.POST("/api/tasks/{id}/retire", {
        params: { path: { id: String(id) } },
        body: { reason: retireReason.trim(), idempotency_key: newKey() },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response
          .json()
          .then((j) => (j as { detail?: string }).detail)
          .catch(() => undefined);
        setRowError(detail ?? `retire failed (${response.status})`);
        return;
      }
      setRetiringId(null);
      setRetireReason("");
      await load();
    } catch (err) {
      setRowError(err instanceof Error ? err.message : "retire failed");
    } finally {
      setRowBusy(false);
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">Tasks</h1>
          <p className="mt-1 text-sm text-muted">
            Every task in scope — pick a project, then create, edit, or
            retire. Claimed or finished work refuses edits; done work can
            never be retired.
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          <Link
            href="/workspace"
            className="rounded-lg border border-steel/40 px-2.5 py-1 text-xs text-cloud transition-colors hover:border-brand/50"
          >
            Back to board
          </Link>
          <Button onClick={() => setShowCreate((v) => !v)} variant="secondary">
            {showCreate ? "Close" : "New task"}
          </Button>
        </div>
      </div>

      {showCreate ? (
        <form
          onSubmit={createTask}
          className="mb-6 space-y-3 rounded-2xl border border-steel/40 bg-abyss/60 p-4"
        >
          <label className="block">
            <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
              Title
            </span>
            <input
              value={cTitle}
              onChange={(e) => setCTitle(e.target.value)}
              placeholder="What should the pack do?"
              aria-label="New task title"
              className="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                Project (optional — defaults to your session project)
              </span>
              <Combobox
                id="tasks-project-combobox"
                value={cProject}
                onChange={setCProject}
                options={projectOptions.filter((n) => n !== "All")}
                ariaLabel="New task project — type to filter"
                placeholder="project name…"
                inputClassName="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                Intent (optional)
              </span>
              <input
                value={cIntent}
                onChange={(e) => setCIntent(e.target.value)}
                placeholder="Why does this matter?"
                aria-label="New task intent"
                className="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
              />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
              Acceptance criteria — one per line (required)
            </span>
            <textarea
              value={cCriteria}
              onChange={(e) => setCCriteria(e.target.value)}
              rows={3}
              placeholder={"The bot can log in\nThe board shows the new task"}
              aria-label="New task acceptance criteria, one per line"
              className="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
            />
          </label>
          <div className="flex items-center gap-3">
            <Button
              type="submit"
              disabled={cBusy || !cTitle.trim() || cCriteria.trim().length === 0}
            >
              {cBusy ? "Creating…" : "Create task"}
            </Button>
            {cError ? <p className="text-sm text-danger">{cError}</p> : null}
          </div>
        </form>
      ) : null}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search titles or ids…"
          aria-label="Search tasks by title or id"
          className="min-w-0 flex-1 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
        />
        <Combobox
          id="tasks-project-filter"
          value={projectInput}
          onChange={(v) => {
            setProjectInput(v);
            if (v === "" || v === "All projects") setProjectFilter("All");
            else if (projectOptions.includes(v)) setProjectFilter(v);
          }}
          onBlur={() => {
            if (!projectOptions.includes(projectInput) && projectInput !== "All projects") {
              setProjectInput("All projects");
              setProjectFilter("All");
            }
          }}
          options={projectOptions.map((name) => (name === "All" ? "All projects" : name))}
          ariaLabel="Filter by project — type to filter"
          placeholder="All projects"
        />
      </div>

      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {rowError ? <p className="mb-4 text-sm text-danger">{rowError}</p> : null}
      {loading ? (
        <p className="font-mono text-sm text-muted">loading…</p>
      ) : visible.length === 0 && !error ? (
        <p className="rounded-2xl border border-steel/40 bg-abyss/60 p-6 text-center text-sm text-muted">
          No tasks in this view — try another project or create one above.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {visible.map((t) => (
            <li
              key={t.id}
              className="rounded-2xl border border-steel/30 bg-navy/60 px-4 py-3"
            >
              {editingId === t.id ? (
                <div className="space-y-2">
                  <input
                    value={eTitle}
                    onChange={(e) => setETitle(e.target.value)}
                    aria-label={`Edit title for task ${t.id}`}
                    className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud focus:border-brand/60 focus:outline-none"
                  />
                  <input
                    value={eIntent}
                    onChange={(e) => setEIntent(e.target.value)}
                    placeholder="Intent (optional)"
                    aria-label={`Edit intent for task ${t.id}`}
                    className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                  />
                  <div className="flex gap-2">
                    <Button
                      onClick={() => void saveEdit(t.id)}
                      disabled={rowBusy || !eTitle.trim()}
                    >
                      {rowBusy ? "Saving…" : "Save"}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => setEditingId(null)}
                      disabled={rowBusy}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : retiringId === t.id ? (
                <div className="space-y-2">
                  <p className="text-sm text-cloud">
                    Retire task #{t.id}? Done work can never be retired —
                    this moves it to the terminal retired state.
                  </p>
                  <input
                    value={retireReason}
                    onChange={(e) => setRetireReason(e.target.value)}
                    placeholder="Reason (required) — e.g. superseded by task 190"
                    aria-label={`Retire reason for task ${t.id}`}
                    className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                  />
                  <div className="flex gap-2">
                    <Button
                      onClick={() => void confirmRetire(t.id)}
                      disabled={rowBusy || !retireReason.trim()}
                    >
                      {rowBusy ? "Retiring…" : "Retire with reason"}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => setRetiringId(null)}
                      disabled={rowBusy}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <Link
                      href={`/workspace/tasks/${t.id}`}
                      className="block truncate text-base font-semibold text-white hover:underline"
                    >
                      {t.title}
                    </Link>
                    <p className="mt-1 font-mono text-[11px] text-muted">
                      #{t.id} · {t.project}
                    </p>
                  </div>
                  <span className="flex shrink-0 items-center gap-2">
                    <TaskStatePill state={t.state} />
                    {t.state === "open" ? (
                      <button
                        type="button"
                        onClick={() => void claimTask(t.id)}
                        className="rounded-lg border border-steel/40 px-2 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud"
                      >
                        Claim
                      </button>
                    ) : null}
                    {t.state === "open" ? (
                      <button
                        type="button"
                        onClick={() => startEdit(t)}
                        className="rounded-lg border border-steel/40 px-2 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud"
                      >
                        Edit
                      </button>
                    ) : null}
                    {t.state !== "done" && t.state !== "retired" ? (
                      <button
                        type="button"
                        onClick={() => {
                          setRowError("");
                          setEditingId(null);
                          setRetireReason("");
                          setRetiringId(t.id);
                        }}
                        className="rounded-lg border border-steel/40 px-2 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud"
                      >
                        Retire
                      </button>
                    ) : null}
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
