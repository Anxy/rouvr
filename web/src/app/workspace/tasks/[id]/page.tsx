"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import {
  csrfHeaders,
  parseSseFrames,
  pumpStream,
  ws,
  type TaskDetail,
} from "@/lib/api/workspace";

interface LiveEvent {
  id: number;
  type: string;
  task_id: number | null;
  created_at: string;
  summary: string;
}

const APPROVABLE = new Set(["claimed", "needs_review"]);
const RETRYABLE = new Set(["failed", "blocked"]);

export default function TaskPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    setError("");
    try {
      const { data, response } = await ws.GET("/api/tasks/{id}", {
        params: { path: { id } },
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        setError(`task not found (${response.status})`);
        return;
      }
      setTask(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "task load failed");
    }
  }, [id, router]);

  useEffect(() => {
    // Mount fetch: single-shot task load, not a render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  // Live tail: only real ledger events for this task.
  useEffect(() => {
    const abort = new AbortController();
    abortRef.current = abort;
    let all = "";
    (async () => {
      try {
        const { response } = await ws.GET("/api/events", {
          parseAs: "stream",
          params: { query: { live: "1", limit: "100" } as never },
        });
        if (!response.ok || !response.body) return;
        await pumpStream(
          response,
          (text) => {
            all += text;
            const rows: LiveEvent[] = [];
            for (const frame of parseSseFrames(all)) {
              if (!frame.event || frame.event === "error") continue;
              try {
                const env = JSON.parse(frame.data) as {
                  id: number;
                  type: string;
                  task_id: number | null;
                  created_at: string;
                  payload: unknown;
                };
                if (env.task_id !== Number(id)) continue;
                const payload = env.payload as { title?: string; from?: string; to?: string };
                const summary =
                  payload?.title ??
                  (payload?.from && payload?.to
                    ? `${payload.from} → ${payload.to}`
                    : env.type);
                rows.push({
                  id: env.id,
                  type: env.type,
                  task_id: env.task_id,
                  created_at: env.created_at,
                  summary,
                });
              } catch {
                /* partial frame: wait for more bytes */
              }
            }
            const seen = new Map<number, LiveEvent>();
            for (const row of rows) seen.set(row.id, row);
            setEvents([...seen.values()].sort((a, b) => a.id - b.id));
          },
          abort.signal,
        );
      } catch {
        /* stream closed: the snapshot below still shows stored rows */
      }
    })();
    return () => abort.abort();
  }, [id]);

  async function transition(to: string) {
    setActionError("");
    setBusy(true);
    try {
      const { response } = await ws.POST("/api/tasks/{id}/transition", {
        params: { path: { id } },
        body: { to },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setActionError(detail || `transition failed (${response.status})`);
        return;
      }
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "transition failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => () => abortRef.current?.abort(), []);

  return (
    <div className="mx-auto w-full max-w-3xl">
      <Link
        href="/workspace"
        className="font-mono text-xs uppercase tracking-wider text-muted hover:text-white"
      >
        ← board
      </Link>
      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}
      {!task && !error ? (
        <p className="mt-4 font-mono text-sm text-muted">loading…</p>
      ) : null}
      {task ? (
        <div className="mt-4 space-y-6">
          <div>
            <div className="mb-2 flex items-center gap-3">
              <span className="font-mono text-sm text-muted">#{task.id}</span>
              <StatusPill
                status={
                  task.state === "done"
                    ? "passed"
                    : task.state === "needs_review"
                      ? "review"
                      : task.state === "failed" || task.state === "blocked"
                        ? "failed"
                        : task.state === "claimed"
                          ? "running"
                          : "queued"
                }
                label={task.state}
              />
            </div>
            <h1 className="text-2xl font-semibold text-white">{task.title}</h1>
            <p className="mt-2 font-mono text-xs text-muted">
              {task.tenant} / {task.project} · by {task.created_by}
            </p>
            <p className="mt-3 text-sm text-cloud">{task.intent}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {APPROVABLE.has(task.state) ? (
              <Button
                variant="primary"
                disabled={busy}
                onClick={() => void transition("done")}
              >
                {busy ? "Working…" : "Approve → done"}
              </Button>
            ) : null}
            {RETRYABLE.has(task.state) ? (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => void transition("open")}
              >
                {busy ? "Working…" : "Retry → open"}
              </Button>
            ) : null}
          </div>
          {actionError ? <p className="text-sm text-danger">{actionError}</p> : null}
          <section>
            <h2 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
              Live events
            </h2>
            {events.length === 0 ? (
              <p className="font-mono text-xs text-muted/60">
                tailing… new ledger events for this task appear here.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {events.map((e) => (
                  <li
                    key={e.id}
                    className="rounded-lg border border-steel/30 bg-abyss/60 px-3 py-2 font-mono text-xs text-cloud"
                  >
                    <span className="text-muted">#{e.id}</span> {e.type}{" "}
                    <span className="text-muted">{e.summary}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {task.contracts.length > 0 ? (
            <section>
              <h2 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
                Acceptance criteria
              </h2>
              <pre className="overflow-x-auto rounded-xl border border-steel/30 bg-abyss/60 p-3 font-mono text-xs text-cloud">
                {task.contracts.map((c) => c.body).join("\n")}
              </pre>
            </section>
          ) : null}
          {task.evidence.length > 0 ? (
            <section>
              <h2 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
                Evidence ({task.evidence.length})
              </h2>
              <ul className="space-y-1.5">
                {task.evidence.slice(-8).map((e) => (
                  <li
                    key={e.id}
                    className="rounded-lg border border-steel/30 bg-abyss/60 px-3 py-2 font-mono text-xs text-cloud"
                  >
                    <span className="text-muted">
                      #{e.id} {e.kind}
                    </span>{" "}
                    {e.body.slice(0, 160)}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
