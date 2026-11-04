"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import { Button } from "@/components/ui/Button";
import {
  csrfHeaders,
  parseSseFrames,
  pumpStream,
  ws,
  type TaskDetail,
} from "@/lib/api/workspace";
import {
  attentionFromEvidence,
  collectKnowledgeRefs,
  describeBlockedReason,
  evidenceLabel,
  formatEvidenceTime,
  journeyStages,
  parseContractCriteria,
  retireReason,
  whyTaskExists,
} from "@/lib/task-detail";

interface LiveEvent {
  id: number;
  type: string;
  task_id: number | null;
  created_at: string;
  summary: string;
}

const APPROVABLE = new Set(["needs_review"]);
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
  const [citeTitles, setCiteTitles] = useState<Record<string, string>>({});
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

  // Derived display data: all real rows, no invented text.
  const refs = task
    ? collectKnowledgeRefs(task.contracts, task.receipts)
    : { planSlugs: [] as string[], categories: [] as string[], citations: [] as string[] };
  const reasonRaw = task ? attentionFromEvidence(task.evidence, task.attention) : null;
  const retiredReason = task ? retireReason(task.evidence) : null;
  const reason = reasonRaw ? describeBlockedReason(reasonRaw) : null;
  const showAttention =
    task !== null &&
    (task.state === "failed" || task.state === "blocked" || task.state === "needs_review");

  // Citation titles resolve live via the knowledge endpoint; a citation
  // whose entry is unreachable keeps its bare citation (no invented text).
  useEffect(() => {
    const ids = refs.citations
      .map((c) => c.match(/^knowledge:(\d+)@v\d+$/)?.[1])
      .filter((v): v is string => !!v && !(v in citeTitles));
    if (ids.length === 0 || !task) return;
    let cancelled = false;
    void (async () => {
      const titles: Record<string, string> = {};
      for (const kid of [...new Set(ids)]) {
        try {
          const { data, response } = await ws.GET("/api/knowledge/{key}", {
            params: { path: { key: kid } },
          });
          if (!response.ok || !data?.title) continue;
          titles[`knowledge:${kid}@v${data.version}`] = data.title;
          // The entry resolves forward to its latest version, so record
          // the requested citation too when the version differs.
          const want = refs.citations.find((c) => c.startsWith(`knowledge:${kid}@v`));
          if (want && !titles[want]) titles[want] = data.title;
        } catch {
          /* unreachable entry: the bare citation still renders */
        }
      }
      if (!cancelled && Object.keys(titles).length > 0) {
        setCiteTitles((prev) => ({ ...prev, ...titles }));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.id, refs.citations.join("|")]);

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

  async function seal() {
    setActionError("");
    setBusy(true);
    try {
      const { response } = await ws.POST("/api/tasks/{id}/seal", {
        params: { path: { id } },
        body: {},
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setActionError(detail || `seal failed (${response.status})`);
        return;
      }
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "seal failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => () => abortRef.current?.abort(), []);

  const stages = task
    ? journeyStages({
        state: task.state,
        contracts: task.contracts,
        evidence: task.evidence,
        receipts: task.receipts,
      })
    : [];

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
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <span className="font-mono text-xs text-dim">#{task.id}</span>
              <TaskStatePill state={task.state} />
              <span className="font-mono text-[11px] text-dim">
                {task.tenant} / {task.project} · by {task.created_by}
              </span>
            </div>
            <h1 className="text-3xl font-semibold leading-tight text-white">{task.title}</h1>
            <p data-why className="mt-2 max-w-2xl text-[15px] leading-relaxed text-cloud">
              {whyTaskExists(task)}
            </p>
            {task.pipeline ? (
              <div className="mt-4">
                <PipelineStrip pipeline={task.pipeline} variant="full" />
              </div>
            ) : null}
            {task.sealing ? (
              <p
                data-seal-badge
                className="mt-2 font-mono text-xs text-muted"
              >
                Sealing hidden checks…{" "}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void seal()}
                  className="underline hover:text-white"
                >
                  {busy ? "Working…" : "Retry seal"}
                </button>
              </p>
            ) : null}
            {task.needs_spec ? (
              <p data-spec-badge className="mt-2 font-mono text-xs text-muted">
                Needs exec spec — the worker host has no spec for this project yet.
              </p>
            ) : null}
            <p className="mt-3 max-w-2xl whitespace-pre-wrap text-sm leading-relaxed text-dim">
              {task.intent}
            </p>
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
          {task.state === "retired" ? (
            <section
              data-retire-badge
              aria-label="Retired"
              className="rounded-2xl border border-steel/30 bg-abyss/60 px-4 py-3"
            >
              <h2 className="mb-1 font-mono text-xs uppercase tracking-wider text-muted">
                Retired
              </h2>
              <p className="text-sm text-cloud">
                {retiredReason ?? "no reason recorded"}
              </p>
            </section>
          ) : null}
          {showAttention ? (
            <section
              data-blocked-reason
              aria-label="Needs attention"
              className="rounded-2xl border border-steel/30 bg-abyss/60 px-4 py-3"
            >
              <h2 className="mb-1 font-mono text-xs uppercase tracking-wider text-muted">
                Needs attention
              </h2>
              <p data-reason-display className="text-sm text-cloud">
                {reason ? reason.display : "reason unavailable"}
              </p>
              {RETRYABLE.has(task.state) ? (
                <p
                  data-retry-note
                  className="mt-1 font-mono text-xs text-muted"
                >
                  Retry reopens it for a worker with the same contract.
                </p>
              ) : null}
            </section>
          ) : null}
          {refs.planSlugs.length + refs.categories.length + refs.citations.length > 0 ? (
            <section
              data-knowledge-badges
              aria-label="Knowledge applied"
              className="rounded-2xl border border-line bg-panel p-4"
            >
              <h2 className="mb-1 text-base font-semibold text-white">
                Knowledge applied
              </h2>
              <p className="mb-3 text-[13px] text-dim">
                Project memory this task builds on — same entries the Knowledge page shows.
              </p>
              <ul className="flex flex-wrap gap-2">
                {refs.planSlugs.map((slug) => (
                  <li
                    key={`plan:${slug}`}
                    data-plan-badge={slug}
                    className="rounded-full border border-steel/40 bg-navy/60 px-3 py-1 font-mono text-xs text-cloud"
                  >
                    plan_slug:{slug}
                  </li>
                ))}
                {refs.categories.map((category) => (
                  <li
                    key={`cat:${category}`}
                    data-category-badge={category}
                    className="rounded-full border border-steel/40 bg-navy/60 px-3 py-1 font-mono text-xs text-cloud"
                  >
                    category:{category}
                  </li>
                ))}
                {refs.citations.map((citation) => (
                  <li
                    key={citation}
                    data-citation-badge={citation}
                    title={citeTitles[citation] ?? undefined}
                    className="rounded-full border border-steel/40 bg-navy/60 px-3 py-1 font-mono text-xs text-cloud"
                  >
                    {citation}
                    {citeTitles[citation] ? ` '${citeTitles[citation]}'` : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          <section data-journey aria-label="Delivery journey">
            <h2 className="mb-3 text-base font-semibold text-white">Delivery journey</h2>
            <ol className="space-y-0">
              {stages.map((s, i) => (
                <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0">
                  {i < stages.length - 1 ? (
                    <span
                      aria-hidden
                      className={`absolute left-[7px] top-5 h-[calc(100%-16px)] w-px ${
                        s.state === "done" ? "bg-brand/50" : "bg-line"
                      }`}
                    />
                  ) : null}
                  <span
                    aria-hidden
                    className={`mt-1 grid h-[15px] w-[15px] shrink-0 place-items-center rounded-full border text-[9px] leading-none ${
                      s.state === "done"
                        ? "border-brand bg-brand/20 text-brand-300"
                        : s.state === "current"
                          ? "border-amber-400 bg-amber-400/20 text-amber-300"
                          : "border-line2 text-dim"
                    }`}
                  >
                    {s.state === "done" ? "✓" : s.state === "current" ? "•" : ""}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p
                      className={`text-sm font-medium ${
                        s.state === "todo" ? "text-dim" : "text-fog"
                      }`}
                    >
                      {s.label}
                      {s.detail ? (
                        <span className="ml-2 font-mono text-[11px] font-normal text-dim">
                          {s.detail}
                        </span>
                      ) : null}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <details className="mt-3">
              <summary className="cursor-pointer font-mono text-[11px] text-dim hover:text-fog">
                raw live events ({events.length})
              </summary>
              {events.length === 0 ? (
                <p className="mt-2 font-mono text-xs text-dim">
                  tailing… new ledger events for this task appear here.
                </p>
              ) : (
                <ul className="mt-2 space-y-1.5">
                  {events.map((e) => (
                    <li
                      key={e.id}
                      className="rounded-lg border border-line bg-abyss/60 px-3 py-2 font-mono text-xs text-fog"
                    >
                      <span className="text-dim">#{e.id}</span> {e.type}{" "}
                      <span className="text-dim">{e.summary}</span>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </section>
          {task.contracts.length > 0 ? (
            <section
              data-contract-card
              aria-label="Agreed outcome"
              className="rounded-2xl border border-line bg-panel p-4"
            >
              <h2 className="mb-1 text-base font-semibold text-white">Agreed outcome</h2>
              <p className="mb-3 text-[13px] text-dim">
                The acceptance criteria this task is verified against.
              </p>
              {(() => {
                const criteria = task.contracts
                  .map((c) => parseContractCriteria(c.body))
                  .find((list) => list !== null);
                if (!criteria) {
                  return (
                    <pre className="overflow-x-auto rounded-xl border border-line bg-abyss/60 p-3 font-mono text-xs text-fog">
                      {task.contracts.map((c) => c.body).join("\n")}
                    </pre>
                  );
                }
                return (
                  <ul className="space-y-2">
                    {criteria.map((c, i) => (
                      <li key={i} className="flex items-start gap-2.5">
                        <span
                          aria-hidden
                          className="mt-0.5 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-md border border-brand/50 bg-brand/10 text-[11px] leading-none text-brand-300"
                        >
                          ✓
                        </span>
                        <span className="text-sm leading-relaxed text-fog">{c}</span>
                      </li>
                    ))}
                  </ul>
                );
              })()}
            </section>
          ) : null}
          {task.evidence.length > 0 ? (
            <section aria-label="Evidence timeline">
              <h2 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
                Evidence ({task.evidence.length})
              </h2>
              <ol className="space-y-1.5">
                {task.evidence.slice(-8).map((e) => (
                  <li
                    key={e.id}
                    data-evidence-entry={e.id}
                    className="rounded-lg border border-steel/30 bg-abyss/60 px-3 py-2 font-mono text-xs text-cloud"
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-muted">
                        #{e.id} {e.kind}
                      </span>
                      <time dateTime={e.created_at} className="shrink-0 text-muted/60">
                        {formatEvidenceTime(e.created_at)}
                      </time>
                    </div>
                    <p className="mt-1 text-cloud">{evidenceLabel(e.kind, e.body)}</p>
                    <details className="mt-1">
                      <summary className="cursor-pointer text-[11px] text-muted">
                        raw JSON
                      </summary>
                      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all text-[11px] text-muted">
                        {e.body}
                      </pre>
                    </details>
                  </li>
                ))}
              </ol>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
