"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import {
  csrfHeaders,
  ws,
  type PlanDetail,
  type PlanTaskCard,
} from "@/lib/api/workspace";

// Execution view: observe one materialized plan's slice->tasks chain.
// Polls the derived plan status every 3 s (timer cleared on unmount);
// no new orchestration engine — dispatch assigns chain tasks to a worker
// principal through the plan execute endpoint, and this page watches the
// same chain states the board and task detail pages already render.
const POLL_MS = 3000;

function groupOf(state: string): "working" | "next" | "done" | "attention" {
  if (state === "done") return "done";
  if (state === "stuck") return "attention";
  if (state === "claimed" || state === "needs_review") return "working";
  return "next";
}

const GROUP_LABEL: Record<string, string> = {
  working: "Working now",
  next: "Up next",
  done: "Completed",
  attention: "Needs attention",
};

function TaskCard({ task }: { task: PlanTaskCard }) {
  return (
    <li className="rounded-xl border border-line bg-abyss/60 px-3 py-2">
      <Link href={task.link} className="text-[13px] font-medium text-fog hover:text-white">
        #{task.id} {task.title}
      </Link>
      <p className="mt-0.5 font-mono text-[11px] text-dim">
        {task.state}
        {task.worker ? ` · worker ${task.worker}` : ""}
      </p>
    </li>
  );
}

export default function ExecutePage() {
  const router = useRouter();
  // Read once: the lint gate forbids setState-in-effect, and the slug
  // never changes for the life of this view.
  const [slug] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("plan"),
  );
  const [error, setError] = useState(() =>
    typeof window !== "undefined" &&
    !new URLSearchParams(window.location.search).get("plan")
      ? "No plan selected — pick one from the planner."
      : "",
  );
  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [worker, setWorker] = useState("");
  const [dispatchBusy, setDispatchBusy] = useState(false);
  const [dispatchMsg, setDispatchMsg] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(
    async (plan: string) => {
      try {
        const { data, response } = await ws.GET("/api/plans/{slug}", {
          params: { path: { slug: plan } },
        });
        if (response.status === 401) {
          router.push("/workspace/login");
          return;
        }
        if (!response.ok || !data) {
          setError(`plan lookup failed (${response.status})`);
          return;
        }
        setDetail(data);
        setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "plan lookup failed");
      }
    },
    [router],
  );

  useEffect(() => {
    if (!slug) return;
    // Deferred so the initial fetch runs outside the effect body
    // (lint gate: no setState-in-effect); the interval owns the poll.
    const kick = setTimeout(() => void load(slug), 0);
    timer.current = setInterval(() => void load(slug), POLL_MS);
    return () => {
      clearTimeout(kick);
      if (timer.current) clearInterval(timer.current);
    };
  }, [load, slug]);

  async function dispatch() {
    if (!slug) return;
    setDispatchBusy(true);
    setDispatchMsg("");
    try {
      const { data, response } = await ws.POST("/api/plans/{slug}/execute", {
        params: { path: { slug } },
        body: { ...(worker.trim() ? { worker: worker.trim() } : {}) },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        const detail = await response.text().catch(() => "");
        setDispatchMsg(detail || `dispatch failed (${response.status})`);
        return;
      }
      setDetail(data);
      const bits = [`dispatched ${data.dispatched.length} task(s)`];
      if (data.assigned.length > 0) bits.push(`assigned ${data.assigned.length}`);
      if (data.contested.length > 0)
        bits.push(`${data.contested.length} contested (held by another worker)`);
      setDispatchMsg(bits.join(" · ") + ".");
    } catch (err) {
      setDispatchMsg(err instanceof Error ? err.message : "dispatch failed");
    } finally {
      setDispatchBusy(false);
    }
  }

  const groups: Record<string, PlanTaskCard[]> = {
    working: [],
    next: [],
    done: [],
    attention: [],
  };
  for (const t of detail?.tasks ?? []) groups[groupOf(t.state)].push(t);
  const anyWorker = (detail?.tasks ?? []).some((t) => t.worker);

  return (
    <div className="mx-auto w-full max-w-4xl">
      <p className="font-mono text-[10px] uppercase tracking-widest text-cyan2">
        Watch the work
      </p>
      <h1 className="mt-1 text-2xl font-semibold text-white">Execute</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        {detail
          ? `${detail.entry.title} · ${detail.status}`
          : "Live view of one materialized plan."}{" "}
        Refreshes every 3 seconds.
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Link
          href="/workspace/planner"
          className="rounded-full border border-line2 px-4 py-1.5 text-sm text-fog transition-colors hover:border-brand/60 hover:text-white"
        >
          ← Back to planner
        </Link>
        {detail?.slice ? (
          <Link
            href={detail.slice.link}
            className="rounded-full border border-line2 px-4 py-1.5 text-sm text-fog transition-colors hover:border-brand/60 hover:text-white"
          >
            Slice #{detail.slice.id}
          </Link>
        ) : null}
      </div>

      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}

      {detail ? (
        <>
          <div
            data-execute-dispatch
            className="mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-panel p-4"
          >
            <input
              value={worker}
              onChange={(e) => setWorker(e.target.value)}
              placeholder="Worker name (optional)"
              aria-label="Worker name for dispatch"
              className="min-w-0 flex-1 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
            />
            <Button
              type="button"
              data-dispatch
              disabled={dispatchBusy || detail.status !== "materialized"}
              onClick={() => void dispatch()}
            >
              {dispatchBusy ? "Dispatching…" : "Dispatch"}
            </Button>
          </div>
          {dispatchMsg ? (
            <p className="mt-2 text-sm text-fog" role="status">
              {dispatchMsg}
            </p>
          ) : null}
          {!anyWorker && detail.status === "materialized" ? (
            <p
              data-waiting-worker
              className="mt-3 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-[13px] text-warning"
            >
              Waiting for a worker — tasks are open and claimable, no worker
              has picked one up yet.
            </p>
          ) : null}
          <div data-execute-chain className="mt-4 space-y-4">
            {(["working", "next", "attention", "done"] as const).map((g) => (
              <section key={g} aria-label={GROUP_LABEL[g]}>
                <h2 className="mb-2 font-mono text-[10px] uppercase tracking-widest text-dim">
                  {GROUP_LABEL[g]} · {groups[g].length}
                </h2>
                {groups[g].length === 0 ? (
                  <p className="text-[13px] text-dim">—</p>
                ) : (
                  <ul className="space-y-1.5">
                    {groups[g].map((t) => (
                      <TaskCard key={t.id} task={t} />
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
