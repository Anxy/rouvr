"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import {
  DeliveryStartDialog,
  type PlanStartTarget,
} from "@/components/workspace/DeliveryStartDialog";
import { GitGrantPanel } from "@/components/workspace/GitGrantPanel";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import {
  csrfHeaders,
  ws,
  type PlanDetail,
  type PlanTaskCard,
} from "@/lib/api/workspace";

const POLL_MS = 3000;

function groupOf(state: string): "working" | "next" | "done" | "attention" {
  if (state === "done") return "done";
  if (state === "stuck" || state === "blocked" || state === "failed") return "attention";
  if (state === "claimed" || state === "needs_review") return "working";
  return "next";
}

const GROUP_LABEL: Record<string, string> = {
  working: "Working now",
  next: "Up next",
  done: "Completed",
  attention: "Needs attention",
};

function TaskCard({
  task,
  startStatus,
}: {
  task: PlanTaskCard;
  startStatus?: string;
}) {
  return (
    <li className="rounded-xl border border-line bg-abyss/60 px-3 py-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <Link href={task.link} className="text-[13px] font-medium text-fog hover:text-white">
          #{task.id} {task.title}
        </Link>
      </div>
      <p className="mt-0.5 font-mono text-[11px] text-dim">
        {task.state}
        {task.worker ? ` · worker ${task.worker}` : ""}
        {startStatus ? ` · ${startStatus}` : ""}
      </p>
      {task.pipeline ? (
        <div className="mt-1">
          <PipelineStrip pipeline={task.pipeline} variant="mini" />
        </div>
      ) : null}
      {task.pr_url ? (
        <a
          href={task.pr_url}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-block font-mono text-[11px] text-brand-300 hover:underline"
        >
          Open pull request ↗
        </a>
      ) : null}
      {task.waiting_git_grant ? (
        <div className="mt-2">
          <GitGrantPanel taskId={task.id} />
        </div>
      ) : null}
      {task.clone_failed ? (
        <p className="mt-1 text-[12px] text-danger">Clone failed: {task.clone_failed}</p>
      ) : null}
    </li>
  );
}

/** Plan-scoped delivery watch: poll chain, Start delivery, Record execute. */
export function PlanDeliveryPanel({ planSlug }: { planSlug: string }) {
  const router = useRouter();
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [dispatchBusy, setDispatchBusy] = useState(false);
  const [dispatchMsg, setDispatchMsg] = useState("");
  const [startPlan, setStartPlan] = useState<PlanStartTarget | null>(null);
  const [startStatuses, setStartStatuses] = useState<Record<number, string>>({});
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
    const kick = setTimeout(() => void load(planSlug), 0);
    timer.current = setInterval(() => void load(planSlug), POLL_MS);
    return () => {
      clearTimeout(kick);
      if (timer.current) clearInterval(timer.current);
    };
  }, [load, planSlug]);

  async function recordExecute() {
    setDispatchBusy(true);
    setDispatchMsg("");
    try {
      const { data, response } = await ws.POST("/api/plans/{slug}/execute", {
        params: { path: { slug: planSlug } },
        body: {},
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data) {
        setDispatchMsg(`dispatch failed (${response.status})`);
        return;
      }
      setDetail(data);
      setDispatchMsg(`Recorded execute for ${data.dispatched.length} open task(s).`);
    } catch (err) {
      setDispatchMsg(err instanceof Error ? err.message : "dispatch failed");
    } finally {
      setDispatchBusy(false);
    }
  }

  function openPlanStart() {
    if (!detail) return;
    setStartPlan({
      slug: planSlug,
      title: detail.entry.title,
      tenant: detail.tenant,
      project: detail.project,
    });
  }

  const groups: Record<string, PlanTaskCard[]> = {
    working: [],
    next: [],
    done: [],
    attention: [],
  };
  for (const t of detail?.tasks ?? []) groups[groupOf(t.state)].push(t);
  const startable = (detail?.tasks ?? []).filter(
    (t) =>
      t.state === "open" ||
      t.state === "claimed" ||
      t.state === "blocked" ||
      t.state === "failed",
  );
  const anyWorker = (detail?.tasks ?? []).some((t) => !!t.worker);

  return (
    <div className="mx-auto w-full max-w-4xl">
      <p className="mt-1 text-sm text-muted">
        {detail
          ? `${detail.entry.title} · ${detail.status} · refreshes every 3 seconds`
          : "Loading plan…"}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {detail?.slice ? (
          <Button href={detail.slice.link} variant="secondary">
            Slice #{detail.slice.id}
          </Button>
        ) : null}
      </div>

      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}

      {detail ? (
        <>
          <div
            data-execute-dispatch
            className="mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-panel p-4"
          >
            <Button
              type="button"
              data-start-all
              data-plan-start
              disabled={startable.length === 0 || detail.status !== "materialized"}
              onClick={() => openPlanStart()}
            >
              Start delivery ({startable.length})
            </Button>
            <Button
              type="button"
              variant="secondary"
              data-dispatch
              disabled={dispatchBusy || detail.status !== "materialized"}
              onClick={() => void recordExecute()}
            >
              {dispatchBusy ? "Recording…" : "Record execute only"}
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
              has picked one up yet. Use Start delivery to kick off coding.
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
                      <TaskCard
                        key={t.id}
                        task={t}
                        startStatus={startStatuses[t.id]}
                      />
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </>
      ) : null}

      {startPlan ? (
        <DeliveryStartDialog
          open
          plan={startPlan}
          onClose={() => setStartPlan(null)}
          onDone={(results) => {
            const next: Record<number, string> = {};
            for (const r of results) next[r.id] = r.ok ? `wf ${r.detail}` : r.detail;
            setStartStatuses((s) => ({ ...s, ...next }));
            void load(planSlug);
          }}
        />
      ) : null}
    </div>
  );
}
