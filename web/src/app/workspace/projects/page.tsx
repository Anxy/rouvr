"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/workspace/session";
import { csrfHeaders, ws, type ProjectRow, type TaskRow } from "@/lib/api/workspace";

// Projects index: real tenant projects from GET /api/projects, with
// open/blocked/done counts and last activity derived from the real task
// list (GET /api/tasks). No invented metrics; a project with no tasks
// shows zeroes and "—". The grid pages client-side at 10 per page.
const PAGE_SIZE = 10;

type Filter = "all" | "active" | "archived";

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

function isArchived(row: ProjectRow | undefined): boolean {
  return Number(row?.archived ?? 0) !== 0;
}

export default function ProjectsPage() {
  const router = useRouter();
  const { tenant, project: activeProject, setProject, refresh } = useSession();
  const [names, setNames] = useState<string[]>([]);
  const [meta, setMeta] = useState<Record<string, ProjectRow>>({});
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [newCode, setNewCode] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newDomain, setNewDomain] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  const load = useCallback(
    async (nextFilter: Filter = "all") => {
      setLoading(true);
      setError("");
      try {
        const wantArchived = nextFilter !== "active";
        const [pRes, tRes] = await Promise.all([
          wantArchived
            ? ws.GET("/api/projects", {
                params: { query: { include_archived: "true" } },
              })
            : ws.GET("/api/projects"),
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
        setMeta(Object.fromEntries(pRes.data.projects.map((p) => [p.project, p])));
        setTasks(tRes.data.items);
      } catch (err) {
        setError(err instanceof Error ? err.message : "projects failed");
      } finally {
        setLoading(false);
      }
    },
    [router],
  );

  useEffect(() => {
    // Mount fetch: one projects + tasks snapshot, not a render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load("all");
  }, [load]);

  // Close the project modal on Escape.
  useEffect(() => {
    if (!showCreate) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setShowCreate(false);
        setEditing(null);
      }
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
    let all = [...new Set([...names, ...stats.keys()])].sort();
    if (filter === "active") all = all.filter((n) => !isArchived(meta[n]));
    else if (filter === "archived") all = all.filter((n) => isArchived(meta[n]));
    if (!q) return all;
    return all.filter((n) => {
      if (n.toLowerCase().includes(q)) return true;
      const m = meta[n];
      return [m?.title, m?.code, m?.description, m?.domain].some((f) =>
        (f ?? "").toLowerCase().includes(q),
      );
    });
  }, [names, stats, meta, query, filter]);

  // Client-side 10/page slice; the page clamps to the filtered length so
  // narrowing the search can never strand the grid on an empty page.
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const pageRows = visible.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  function changeFilter(next: Filter) {
    setFilter(next);
    setPage(1);
    void load(next);
  }

  function clearForm() {
    setNewName("");
    setNewTitle("");
    setNewCode("");
    setNewDescription("");
    setNewDomain("");
  }

  function openCreate() {
    setEditing(null);
    clearForm();
    setCreateError("");
    setShowCreate(true);
  }

  function openEdit(name: string) {
    const m = meta[name];
    setEditing(name);
    setNewName(name);
    setNewTitle(m?.title ?? "");
    setNewCode(m?.code ?? "");
    setNewDescription(m?.description ?? "");
    setNewDomain(m?.domain ?? "");
    setCreateError("");
    setShowCreate(true);
  }

  function closeModal() {
    setShowCreate(false);
    setEditing(null);
  }

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    const name = newName.trim();
    if (!name || !tenant) return;
    setCreating(true);
    setCreateError("");
    try {
      const meta: Record<string, string> = {};
      const pairs: Array<[string, string]> = [
        ["title", newTitle],
        ["code", newCode],
        ["description", newDescription],
        ["domain", newDomain],
      ];
      for (const [field, value] of pairs) {
        if (value.trim()) meta[field] = value.trim();
      }
      const { data, response } = await ws.POST("/api/projects", {
        body: { tenant, project: name, ...meta },
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
      clearForm();
      closeModal();
      refresh();
      await load(filter);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "create failed");
    } finally {
      setCreating(false);
    }
  }

  async function saveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    const next = newName.trim();
    if (!next) {
      setCreateError("project name is required");
      return;
    }
    setCreating(true);
    setCreateError("");
    try {
      const { response } = await ws.PATCH("/api/projects/{name}", {
        params: { path: { name: editing } },
        body: {
          name: next,
          title: newTitle.trim(),
          code: newCode.trim(),
          description: newDescription.trim(),
          domain: newDomain.trim(),
        },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (response.status === 409) {
        setCreateError("name taken");
        return;
      }
      if (!response.ok) {
        setCreateError(`update failed (${response.status})`);
        return;
      }
      clearForm();
      closeModal();
      await refresh();
      if (activeProject === editing && next !== editing) setProject(next);
      await load(filter);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "update failed");
    } finally {
      setCreating(false);
    }
  }

  async function toggleArchive(name: string) {
    const archived = isArchived(meta[name]);
    setBusy(name);
    setActionError("");
    try {
      const { response } = await ws.PATCH("/api/projects/{name}", {
        params: { path: { name } },
        body: { archived: !archived },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        setActionError(`update failed (${response.status})`);
        return;
      }
      refresh();
      await load(filter);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "update failed");
    } finally {
      setBusy(null);
    }
  }

  const isEditing = editing !== null;

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
          <Button onClick={openCreate}>Create project</Button>
        </div>
      </div>

      {showCreate ? (
        <div
          data-create-project-modal
          className="fixed inset-0 z-50 flex items-center justify-center bg-abyss p-4"
          onClick={closeModal}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={isEditing ? "Edit project" : "Create project"}
            className="w-full max-w-md rounded-2xl border border-steel/40 bg-navy p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-base font-semibold text-white">
              {isEditing ? "Edit project" : "Create project"}
            </h2>
            <p className="mt-1 text-sm text-muted">
              {isEditing ? `Editing ${editing}` : `New project${tenant ? ` in ${tenant}` : ""}`}
            </p>
            <form onSubmit={isEditing ? saveEdit : createProject} className="mt-4 space-y-3">
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Project name
                </span>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="project name…"
                  aria-label={isEditing ? "Project name (rename)" : "New project name"}
                  autoFocus
                  className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                />
                {isEditing ? (
                  <span className="mt-1 block text-xs text-muted">
                    Renaming moves its tasks along with it.
                  </span>
                ) : null}
              </label>
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Title
                </span>
                <input
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="Display title…"
                  aria-label={isEditing ? "Project title" : "New project title"}
                  className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                    Code
                  </span>
                  <input
                    value={newCode}
                    onChange={(e) => setNewCode(e.target.value)}
                    placeholder="SHORT-CODE…"
                    aria-label={isEditing ? "Project code" : "New project code"}
                    className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                    Domain
                  </span>
                  <input
                    value={newDomain}
                    onChange={(e) => setNewDomain(e.target.value)}
                    placeholder="e.g. web, ops…"
                    aria-label={isEditing ? "Project domain" : "New project domain"}
                    className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                  />
                </label>
              </div>
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Description
                </span>
                <textarea
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="What is this project about…"
                  aria-label={isEditing ? "Project description" : "New project description"}
                  rows={3}
                  className="w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                />
              </label>
              {createError ? (
                <p className="text-sm text-danger">{createError}</p>
              ) : null}
              <div className="flex justify-end gap-2">
                <Button type="button" variant="secondary" onClick={closeModal}>
                  Cancel
                </Button>
                <Button type="submit" disabled={creating || !newName.trim()}>
                  {creating
                    ? isEditing
                      ? "Saving…"
                      : "Creating…"
                    : isEditing
                      ? "Save changes"
                      : "Create project"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-2">
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
        <div role="group" aria-label="Project status filter" className="flex gap-1.5">
          {(["all", "active", "archived"] as const).map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={filter === f}
              onClick={() => changeFilter(f)}
              className={
                filter === f
                  ? "rounded-lg border border-brand/60 bg-brand/10 px-2.5 py-1 text-xs text-cloud transition-colors"
                  : "rounded-lg border border-steel/40 px-2.5 py-1 text-xs text-muted transition-colors hover:border-brand/50 hover:text-cloud"
              }
            >
              {f[0].toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {actionError ? <p className="mb-4 text-sm text-danger">{actionError}</p> : null}
      {loading ? (
        <p className="font-mono text-sm text-muted">loading…</p>
      ) : visible.length === 0 ? (
        <p className="rounded-2xl border border-steel/40 bg-abyss/60 p-6 text-center text-sm text-muted">
          {filter === "archived" ? "No archived projects." : "No projects yet — create one above."}
        </p>
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2">
            {pageRows.map((name) => {
            const s = stats.get(name) ?? emptyStats();
            const m = meta[name];
            const heading = m?.title?.trim() ? m.title : name;
            const archived = isArchived(m);
            return (
              <li
                key={name}
                className="rounded-2xl border border-steel/30 bg-navy/60 px-4 py-3"
              >
                <div className="flex items-baseline gap-2">
                  <h2 className="min-w-0 flex-1 truncate text-base font-semibold text-white">
                    {heading}
                  </h2>
                  {m?.code?.trim() ? (
                    <span className="shrink-0 rounded-full border border-line2 bg-panel2 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-cyan2">
                      {m.code}
                    </span>
                  ) : null}
                  {m?.domain?.trim() ? (
                    <span className="shrink-0 font-mono text-[10px] text-dim">
                      {m.domain}
                    </span>
                  ) : null}
                  {archived ? (
                    <span className="shrink-0 rounded-full border border-line2 bg-panel2 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted">
                      archived
                    </span>
                  ) : null}
                </div>
                {m?.description?.trim() ? (
                  <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-dim">
                    {m.description}
                  </p>
                ) : null}
                <p className="mt-1 font-mono text-[11px] text-muted">
                  {m?.title?.trim() ? `${name} · ` : ""}
                  open {s.open} · attention {s.stuck} · done {s.done} ·
                  last activity {formatWhen(s.lastActivity)}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
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
                  <button
                    type="button"
                    onClick={() => openEdit(name)}
                    className="rounded-lg border border-steel/40 px-2.5 py-1 text-xs text-cloud transition-colors hover:border-brand/50"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => void toggleArchive(name)}
                    disabled={busy === name}
                    className="rounded-lg border border-steel/40 px-2.5 py-1 text-xs text-cloud transition-colors hover:border-brand/50 disabled:opacity-40"
                  >
                    {busy === name ? "…" : archived ? "Restore" : "Archive"}
                  </button>
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
