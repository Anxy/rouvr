"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/workspace/session";
import { csrfHeaders, ws, type TaskRow } from "@/lib/api/workspace";

// Projects index: real tenant projects from GET /api/projects, with
// open/blocked/done counts and last activity derived from the real task
// list (GET /api/tasks). No invented metrics; a project with no tasks
// shows zeroes and "—". The grid pages client-side at 10 per page.
const PAGE_SIZE = 10;
interface ProjectStats {
  open: number;
  stuck: number;
  done: number;
  lastActivity: string;
}

function emptyStats(): ProjectStats {
  return { open: 0, stuck: 0, done: 0, lastActivity: "—" };
}

function formatWhen(iso: string): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleString();
}

export default function ProjectsPage() {
  const router = useRouter();
  const { tenant, refresh } = useSession();
  const [names, setNames] = useState<string[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [pRes, tRes] = await Promise.all([
        ws.GET("/api/projects"),
        ws.GET("/api/tasks"),
      ]);
      if (pRes.response.status === 401 || tRes.response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!pRes.response.ok || !pRes.data) {
        setError(`projects failed (${pRes.response.status})`);
        return;
      }
      if (!tRes.response.ok || !tRes.data) {
        setError(`tasks failed (${tRes.response.status})`);
        return;
      }
      setNames(pRes.data.projects.map((p) => p.project));
      setTasks(tRes.data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "projects failed");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    // Mount fetch: one projects + tasks snapshot, not a render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  // Close the create modal on Escape.
  useEffect(() => {
    if (!showCreate) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowCreate(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showCreate]);

  const stats = useMemo(() => {
    const byProject = new Map<string, ProjectStats>();
    for (const t of tasks) {
      let s = byProject.get(t.project);
      if (!s) {
        s = emptyStats();
        byProject.set(t.project, s);
      }
      if (t.state === "open" || t.state === "claimed") s.open += 1;
      else if (t.state === "blocked" || t.state === "failed" || t.state === "needs_review")
        s.stuck += 1;
      else if (t.state === "done") s.done += 1;
      const ms = Date.parse(t.updated_at);
      const cur = Date.parse(s.lastActivity);
      if (!Number.isNaN(ms) && (s.lastActivity === "—" || ms > cur)) {
        s.lastActivity = t.updated_at;
      }
    }
    return byProject;
  }, [tasks]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = [...new Set([...names, ...stats.keys()])].sort();
    if (!q) return all;
    return all.filter((n) => n.toLowerCase().includes(q));
  }, [names, stats, query]);

  // Client-side 10/page slice; the page clamps to the filtered length so
  // narrowing the search can never strand the grid on an empty page.
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const pageRows = visible.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    const name = newName.trim();
    if (!name || !tenant) return;
    setCreating(true);
    setCreateError("");
    try {
      const { data, response } = await ws.POST("/api/projects", {
        body: { tenant, project: name },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        setCreateError(`create failed (${response.status})`);
        return;
      }
      setNewName("");
      setShowCreate(false);
      refresh();
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "create failed");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">Projects</h1>
          <p className="mt-1 text-sm text-muted">
            Tenant projects with live counts from the ledger — open, needs
            attention, done, and last activity.
          </p>
        </div>
        <div className="ml-auto">
          <Button onClick={() => setShowCreate(true)}>Create project</Button>
        </div>
      </div>

      {showCreate ? (
        <div
          data-create-project-modal
          className="fixed inset-0 z-50 flex items-center justify-center bg-abyss p-4"
          onClick={() => setShowCreate(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Create project"
            className="w-full max-w-md rounded-2xl border border-steel/40 bg-navy p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold text-white">
              Create project
            </h2>
            <p className="mt-1 text-sm text-muted">
              New project{tenant ? ` in ${tenant}` : ""}
            </p>
            <form onSubmit={createProject} className="mt-4 space-y-3">
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Project name
                </span>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="project name…"
                  aria-label="New project name"
                  autoFocus
                  className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                />
              </label>
              {createError ? (
                <p className="text-sm text-danger">{createError}</p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setShowCreate(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={creating || !newName.trim()}>
                  {creating ? "Creating…" : "Create project"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      <div className="mb-4">
        <input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
          placeholder="Search projects…"
          aria-label="Search projects"
          className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none sm:max-w-xs"
        />
      </div>

      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {loading ? (
        <p className="font-mono text-sm text-muted">loading…</p>
      ) : visible.length === 0 ? (
        <p className="rounded-2xl border border-steel/40 bg-abyss/60 p-6 text-center text-sm text-muted">
          No projects yet — create one above.
        </p>
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2">
            {pageRows.map((name) => {
            const s = stats.get(name) ?? emptyStats();
            return (
              <li
                key={name}
                className="rounded-2xl border border-steel/30 bg-navy/60 px-4 py-3"
              >
                <h2 className="truncate text-base font-semibold text-white">
                  {name}
                </h2>
                <p className="mt-1 font-mono text-[11px] text-muted">
                  open {s.open} · attention {s.stuck} · done {s.done} ·
                  last activity {formatWhen(s.lastActivity)}
                </p>
                <div className="mt-2 flex gap-2">
                  <Link
                    href="/workspace"
                    className="rounded-lg border border-steel/40 px-2.5 py-1 text-xs text-cloud transition-colors hover:border-brand/50"
                  >
                    Open in board
                  </Link>
                  <Link
                    href="/workspace/chat"
                    className="rounded-lg border border-steel/40 px-2.5 py-1 text-xs text-cloud transition-colors hover:border-brand/50"
                  >
                    Open in chat
                  </Link>
                </div>
              </li>
            );
          })}
          </ul>
          {totalPages > 1 ? (
            <div
              className="mt-4 flex items-center gap-2"
              role="group"
              aria-label="Projects pages"
            >
              <button
                type="button"
                onClick={() => setPage(safePage - 1)}
                disabled={safePage <= 1}
                aria-label="Projects: previous page"
                className="rounded-lg border border-steel/40 px-2.5 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud disabled:opacity-40 disabled:hover:border-steel/40 disabled:hover:text-muted"
              >
                Prev
              </button>
              <span className="font-mono text-[11px] text-muted">
                Showing {(safePage - 1) * PAGE_SIZE + 1}–
                {Math.min(safePage * PAGE_SIZE, visible.length)} of {visible.length}
              </span>
              <button
                type="button"
                onClick={() => setPage(safePage + 1)}
                disabled={safePage >= totalPages}
                aria-label="Projects: next page"
                className="rounded-lg border border-steel/40 px-2.5 py-1 font-mono text-[11px] text-muted transition-colors hover:border-brand/50 hover:text-cloud disabled:opacity-40 disabled:hover:border-steel/40 disabled:hover:text-muted"
              >
                Next
              </button>
            </div>
          ) : null}
        </>
      )}

      <p className="mt-6 font-mono text-[11px] text-muted/70">
        The guided profile interview is planned, not built yet.
      </p>
    </div>
  );
}
