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
export default function PlannerPage() {
  const router = useRouter();
  const [plans, setPlans] = useState<KnowledgeHit[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);

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
          {plans.map((p) => (
            <li
              key={p.citation}
              className="flex items-center gap-3 rounded-2xl border border-line bg-panel p-4"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[15px] font-semibold text-white">
                  {p.title}
                </p>
                <p className="mt-0.5 truncate font-mono text-[11px] text-dim">
                  {p.citation} · {isVerified(p) ? "agreed" : "draft"}
                </p>
              </div>
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
            </li>
          ))}
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
