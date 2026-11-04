"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ws, type KnowledgeHit } from "@/lib/api/workspace";
import { isVerified } from "@/lib/knowledge";

// Planner: the work surface. Plans are the plan knowledge entries the chat
// saves (titles minted as "Plan: …"); converting happens in chat, so every
// plan links back there as a handoff. No plan lifecycle is invented here:
// verified means agreed, otherwise draft; materialization state lives with
// the slice/tasks the chat Convert creates.
interface PlanThread {
  sliceTitle: string;
  sliceIntent: string;
  criteria: string[];
  tasks: { title: string; intent: string }[];
  raw: string;
}

function parsePlanThread(body: string): PlanThread | null {
  try {
    const parsed: unknown = JSON.parse(body);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const rec = parsed as Record<string, unknown>;
    const slice =
      rec.slice && typeof rec.slice === "object" ? (rec.slice as Record<string, unknown>) : null;
    const rawTasks = Array.isArray(rec.tasks) ? rec.tasks : [];
    const tasks = rawTasks
      .filter((t): t is Record<string, unknown> => !!t && typeof t === "object" && !Array.isArray(t))
      .map((t) => ({
        title: typeof t.title === "string" ? t.title : "untitled step",
        intent: typeof t.intent === "string" ? t.intent : "",
      }));
    if (!slice && tasks.length === 0) return null;
    const criteria = Array.isArray(slice?.acceptance_criteria)
      ? slice!.acceptance_criteria.filter((c): c is string => typeof c === "string")
      : [];
    return {
      sliceTitle: typeof slice?.title === "string" ? slice.title : "",
      sliceIntent: typeof slice?.intent === "string" ? slice.intent : "",
      criteria,
      tasks,
      raw: body,
    };
  } catch {
    return null;
  }
}

export default function PlannerPage() {
  const router = useRouter();
  const [plans, setPlans] = useState<KnowledgeHit[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [openCitation, setOpenCitation] = useState<string | null>(null);
  const [thread, setThread] = useState<PlanThread | null>(null);
  const [threadError, setThreadError] = useState("");

  async function openPlan(hit: KnowledgeHit) {
    if (openCitation === hit.citation) {
      setOpenCitation(null);
      setThread(null);
      return;
    }
    setOpenCitation(hit.citation);
    setThread(null);
    setThreadError("");
    const m = hit.citation.match(/^knowledge:(\d+)@v\d+$/);
    if (!m) {
      setThreadError("cannot open this plan");
      return;
    }
    try {
      const { data, response } = await ws.GET("/api/knowledge/{key}", {
        params: { path: { key: m[1] } },
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !data?.body) {
        setThreadError("plan not found");
        return;
      }
      const parsed = parsePlanThread(data.body);
      setThread(
        parsed ?? { sliceTitle: "", sliceIntent: "", criteria: [], tasks: [], raw: data.body },
      );
    } catch (err) {
      setThreadError(err instanceof Error ? err.message : "plan load failed");
    }
  }

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const { data, response } = await ws.GET("/api/knowledge/search", {
          params: { query: { q: "Plan", n: "50" } },
        });
        if (!live) return;
        if (response.status === 401) {
          router.push("/workspace/login");
          return;
        }
        if (!response.ok || !data) {
          setError(`planner search failed (${response.status})`);
          return;
        }
        setPlans(data.items.filter((h) => h.title.startsWith("Plan: ")));
      } catch (err) {
        if (live) setError(err instanceof Error ? err.message : "planner load failed");
      } finally {
        if (live) setBusy(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [router]);

  return (
    <div className="mx-auto w-full max-w-4xl">
      <p className="font-mono text-[10px] uppercase tracking-widest text-cyan2">
        Agree the plan
      </p>
      <h1 className="mt-1 text-2xl font-semibold text-white">Planner</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        Plans under discussion with your pack. A plan becomes work when you
        convert it in chat — each plan hands off to the thread that shapes it.
      </p>
      <div className="mt-4">
        <Link
          href="/workspace/chat"
          className="inline-flex h-10 items-center rounded-full bg-brand px-5 text-sm font-medium text-abyss transition-colors hover:bg-cyan2"
        >
          Start a new plan in chat
        </Link>
      </div>
      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}
      {busy ? (
        <p className="mt-4 text-sm text-muted" aria-live="polite">
          Loading plans…
        </p>
      ) : plans.length > 0 ? (
        <ul data-plan-list className="mt-4 space-y-2">
          {plans.map((p) => {
            const open = openCitation === p.citation;
            return (
              <li
                key={p.citation}
                data-plan-thread={open ? p.citation : undefined}
                className="rounded-2xl border border-line bg-panel p-4"
              >
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => void openPlan(p)}
                    aria-expanded={open}
                    className="min-w-0 flex-1 text-left"
                  >
                    <p className="truncate text-[15px] font-semibold text-white">
                      {p.title}
                    </p>
                    <p className="mt-0.5 truncate font-mono text-[11px] text-dim">
                      {p.citation} · {isVerified(p) ? "agreed" : "draft"}
                      {open ? " · close" : " · open thread"}
                    </p>
                  </button>
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] ${
                      isVerified(p)
                        ? "border border-brand/40 bg-brand/10 text-brand-300"
                        : "border border-steel/40 bg-abyss text-muted"
                    }`}
                  >
                    {isVerified(p) ? "agreed" : "draft"}
                  </span>
                  <Link
                    href="/workspace/chat"
                    className="shrink-0 rounded-full border border-line2 px-4 py-1.5 text-sm text-fog transition-colors hover:border-brand/60 hover:text-white"
                  >
                    Open in chat →
                  </Link>
                </div>
                {open ? (
                  <div className="mt-3 border-t border-line pt-3">
                    {threadError ? (
                      <p className="text-sm text-danger">{threadError}</p>
                    ) : !thread ? (
                      <p className="text-sm text-dim" aria-live="polite">
                        Loading thread…
                      </p>
                    ) : thread.sliceTitle || thread.tasks.length > 0 ? (
                      <div className="space-y-3">
                        {thread.sliceTitle ? (
                          <div>
                            <p className="text-sm font-semibold text-fog">
                              {thread.sliceTitle}
                            </p>
                            {thread.sliceIntent ? (
                              <p className="mt-0.5 text-[13px] leading-relaxed text-dim">
                                {thread.sliceIntent}
                              </p>
                            ) : null}
                          </div>
                        ) : null}
                        {thread.criteria.length > 0 ? (
                          <ul className="space-y-1.5">
                            {thread.criteria.map((c, i) => (
                              <li key={i} className="flex items-start gap-2">
                                <span
                                  aria-hidden
                                  className="mt-0.5 grid h-[16px] w-[16px] shrink-0 place-items-center rounded-md border border-brand/50 bg-brand/10 text-[10px] leading-none text-brand-300"
                                >
                                  ✓
                                </span>
                                <span className="text-[13px] leading-relaxed text-fog">
                                  {c}
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        {thread.tasks.length > 0 ? (
                          <ol className="space-y-1.5">
                            {thread.tasks.map((t, i) => (
                              <li
                                key={i}
                                className="rounded-xl border border-line bg-abyss/60 px-3 py-2"
                              >
                                <p className="text-[13px] font-medium text-fog">
                                  {i + 1}. {t.title}
                                </p>
                                {t.intent ? (
                                  <p className="mt-0.5 text-xs leading-relaxed text-dim">
                                    {t.intent}
                                  </p>
                                ) : null}
                              </li>
                            ))}
                          </ol>
                        ) : null}
                      </div>
                    ) : (
                      <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs text-fog">
                        {thread.raw.slice(0, 2000)}
                      </pre>
                    )}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        !error && (
          <p className="mt-4 rounded-2xl border border-line bg-panel p-6 text-center text-sm text-muted">
            No plans yet — start one from any chat thread.
          </p>
        )
      )}
    </div>
  );
}
