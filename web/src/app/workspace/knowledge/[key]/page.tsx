"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ws,
  type KnowledgeEntry,
  type KnowledgeUsage,
} from "@/lib/api/workspace";
import { isVerified } from "@/lib/knowledge";

// Knowledge inner page: one entry with its hierarchy (kind, version,
// verification, usage), the full body, and its version history. Every
// value comes from the ledger (detail + history + usage); missing usage
// renders an em dash, never an invented number.
function VerifiedPill({ verified }: { verified: boolean }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] ${
        verified
          ? "border border-brand/40 bg-brand/10 text-brand-300"
          : "border border-steel/40 bg-abyss text-muted"
      }`}
    >
      {verified ? "verified" : "unverified"}
    </span>
  );
}

function usageLine(u: KnowledgeUsage | null | undefined): string {
  if (!u) return "Usage —";
  const n = u.applied_to_tasks;
  const last = u.last_used_at ? new Date(u.last_used_at).toLocaleDateString() : "—";
  return `Applied to ${n} task${n === 1 ? "" : "s"} · Last used ${last}`;
}

export default function KnowledgeEntryPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const router = useRouter();
  const [key, setKey] = useState<string | null>(null);
  const [entry, setEntry] = useState<KnowledgeEntry | null>(null);
  const [history, setHistory] = useState<KnowledgeEntry[]>([]);
  const [usage, setUsage] = useState<KnowledgeUsage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (id: string) => {
      setLoading(true);
      setError("");
      try {
        const [detail, hist, use] = await Promise.all([
          ws.GET("/api/knowledge/{key}", { params: { path: { key: id } } }),
          ws.GET("/api/knowledge/{key}/history", { params: { path: { key: id } } }),
          ws.GET("/api/knowledge/{key}/usage", { params: { path: { key: id } } }),
        ]);
        if (
          detail.response.status === 401 ||
          hist.response.status === 401 ||
          use.response.status === 401
        ) {
          router.push("/workspace/login");
          return;
        }
        if (!detail.response.ok || !detail.data) {
          setError(`entry not found (${detail.response.status})`);
          return;
        }
        setEntry(detail.data);
        if (hist.response.ok && hist.data) setHistory(hist.data.items);
        if (use.response.ok && use.data) setUsage(use.data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "entry load failed");
      } finally {
        setLoading(false);
      }
    },
    [router],
  );

  useEffect(() => {
    let alive = true;
    void params.then(({ key: id }) => {
      if (!alive) return;
      setKey(id);
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void load(id);
    });
    return () => {
      alive = false;
    };
  }, [params, load]);

  return (
    <div className="mx-auto w-full max-w-6xl">
      <Link
        href="/workspace/knowledge"
        className="font-mono text-xs text-muted transition-colors hover:text-cloud"
      >
        ← Back to knowledge
      </Link>
      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}
      {loading ? (
        <p className="mt-4 font-mono text-sm text-muted">loading…</p>
      ) : entry ? (
        <article data-entry-detail className="mt-4 rounded-2xl border border-line bg-panel p-5">
          <p className="font-mono text-[10px] uppercase tracking-widest text-cyan2">
            {entry.kind} · {entry.citation}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="min-w-0 flex-1 text-2xl font-semibold text-white">
              {entry.title}
            </h1>
            <VerifiedPill verified={isVerified(entry)} />
          </div>
          <p className="mt-1 font-mono text-[11px] text-dim" data-version>
            v{entry.version} · by {entry.author}
          </p>
          <p className="mt-1 font-mono text-[11px] text-cyan2">{usageLine(usage)}</p>
          {entry.summary ? (
            <p className="mt-3 text-sm leading-relaxed text-dim">{entry.summary}</p>
          ) : null}
          <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-fog">
            {entry.body}
          </p>
          {history.length > 1 ? (
            <section aria-label="Version history" className="mt-4 border-t border-line pt-3">
              <h2 className="mb-2 font-mono text-[10px] uppercase tracking-widest text-dim">
                Version history
              </h2>
              <ol className="space-y-1.5">
                {history.map((v) => (
                  <li
                    key={v.citation}
                    className="flex items-center gap-2 font-mono text-xs text-dim"
                  >
                    <span className="text-fog">{v.citation}</span>
                    <span className="min-w-0 flex-1 truncate font-sans text-xs">
                      {v.title}
                    </span>
                    <VerifiedPill verified={isVerified(v)} />
                  </li>
                ))}
              </ol>
            </section>
          ) : null}
        </article>
      ) : null}
      {key && !loading && !entry && !error ? (
        <p className="mt-4 text-sm text-muted">No entry loaded.</p>
      ) : null}
    </div>
  );
}
