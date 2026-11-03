"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { StatusPill, type Status } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { clearCsrfToken, ws, type TaskRow } from "@/lib/api/workspace";

const COLUMNS: { key: string; title: string; match: (s: string) => boolean }[] = [
  { key: "open", title: "Open", match: (s) => s === "open" },
  { key: "claimed", title: "Claimed", match: (s) => s === "claimed" },
  { key: "review", title: "Needs review", match: (s) => s === "needs_review" },
  { key: "done", title: "Done", match: (s) => s === "done" },
  { key: "attention", title: "Failed / blocked", match: (s) => s === "failed" || s === "blocked" },
];

function pill(status: string): Status {
  if (status === "done") return "passed";
  if (status === "needs_review") return "review";
  if (status === "failed" || status === "blocked") return "failed";
  if (status === "claimed") return "running";
  return "queued";
}

export default function BoardPage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

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

  async function logout() {
    await ws.POST("/api/auth/logout").catch(() => undefined);
    clearCsrfToken();
    router.push("/workspace/login");
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">Project board</h1>
          <p className="mt-1 text-sm text-muted">
            Every task in your tenant, grouped by state. Only real ledger rows — no samples.
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          <Button href="/workspace/chat" variant="secondary">
            New slice
          </Button>
          <Button variant="ghost" onClick={() => void logout()}>
            Sign out
          </Button>
        </div>
      </div>
      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {loading ? (
        <p className="font-mono text-sm text-muted">loading…</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          {COLUMNS.map((col) => {
            const rows = tasks.filter((t) => col.match(t.state));
            return (
              <section
                key={col.key}
                className="rounded-2xl border border-steel/40 bg-abyss/60 p-3"
              >
                <header className="mb-3 flex items-baseline justify-between px-1">
                  <h2 className="font-mono text-xs uppercase tracking-wider text-muted">
                    {col.title}
                  </h2>
                  <span className="font-mono text-xs text-muted">{rows.length}</span>
                </header>
                <ul className="space-y-2">
                  {rows.map((t) => (
                    <li key={t.id}>
                      <Link
                        href={`/workspace/tasks/${t.id}`}
                        className="block rounded-xl border border-steel/30 bg-navy/60 p-3 transition-colors hover:border-brand/50"
                      >
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <span className="font-mono text-xs text-muted">#{t.id}</span>
                          <StatusPill status={pill(t.state)} label={t.state} />
                        </div>
                        <p className="text-sm text-cloud">{t.title}</p>
                        <p className="mt-1 font-mono text-[11px] text-muted">
                          {t.tenant} / {t.project}
                        </p>
                      </Link>
                    </li>
                  ))}
                  {rows.length === 0 ? (
                    <li className="px-1 py-4 text-center font-mono text-xs text-muted/60">
                      empty
                    </li>
                  ) : null}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
