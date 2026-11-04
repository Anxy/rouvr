"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import {
  ws,
  type KnowledgeEntry,
  type KnowledgeHit,
  type KnowledgeUsage,
} from "@/lib/api/workspace";
import {
  DEFAULT_QUERY,
  KIND_OPTIONS,
  citationId,
  citationVersion,
  countKinds,
  isVerified,
} from "@/lib/knowledge";

// Knowledge browser in the Figma Knowledge shape: a search panel up top,
// kind folders aside, and clickable doc cards in a grid. Every number and
// list comes from the ledger. Per-row usage (applied-task count + last use)
// loads lazily per visible shortlist row; a row that cannot source usage
// renders an em dash, never an invented number. Opening a card loads the
// full entry plus its version history below the grid.

const PAGE_SIZE = "50";

const EMPTY_GUIDE =
  "created from approved decisions, task outcomes and chat — search above to browse";

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

export default function KnowledgePage() {
  const router = useRouter();
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [kind, setKind] = useState("All");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [usage, setUsage] = useState<Record<string, KnowledgeUsage | null>>({});
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<KnowledgeEntry | null>(null);
  const [history, setHistory] = useState<KnowledgeEntry[]>([]);
  const [detailError, setDetailError] = useState("");
  const defaultRan = useRef(false);
  const usageRun = useRef(0);

  const loadUsage = useCallback(
    async (rows: KnowledgeHit[]) => {
      const run = ++usageRun.current;
      const settled = await Promise.allSettled(
        rows.map(async (h) => {
          const id = citationId(h.citation);
          const { data, response } = await ws.GET("/api/knowledge/{key}/usage", {
            params: { path: { key: id } },
          });
          if (run !== usageRun.current) return null;
          if (response.status === 401) {
            router.push("/workspace/login");
            return null;
          }
          return response.ok && data ? { citation: h.citation, usage: data } : null;
        }),
      );
      if (run !== usageRun.current) return;
      const next: Record<string, KnowledgeUsage | null> = {};
      for (const s of settled) {
        if (s.status === "fulfilled" && s.value) next[s.value.citation] = s.value.usage;
      }
      setUsage(next);
    },
    [router],
  );

  const search = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      if (!query.trim() || busy) return;
      setBusy(true);
      setError("");
      setSelected(null);
      setHistory([]);
      setUsage({});
      setDetailError("");
      try {
        const { data, response } = await ws.GET("/api/knowledge/search", {
          params: {
            query: {
              q: query.trim(),
              n: PAGE_SIZE,
            },
          },
        });
        if (response.status === 401) {
          router.push("/workspace/login");
          return;
        }
        if (!response.ok || !data) {
          setError(`knowledge search failed (${response.status})`);
          setHits([]);
          return;
        }
        setHits(data.items);
        setSearched(true);
        void loadUsage(data.items);
      } catch (err) {
        setError(err instanceof Error ? err.message : "knowledge search failed");
        setHits([]);
      } finally {
        setBusy(false);
      }
    },
    [query, busy, router, loadUsage],
  );

  // Default view: list recent entries on mount with a broad query so the
  // page opens with content instead of an empty prompt.
  useEffect(() => {
    if (defaultRan.current) return;
    defaultRan.current = true;
    void search();
  }, [search]);

  const open = useCallback(
    async (hit: KnowledgeHit) => {
      const id = citationId(hit.citation);
      setDetailError("");
      setSelected(null);
      setHistory([]);
      try {
        const [detail, hist] = await Promise.all([
          ws.GET("/api/knowledge/{key}", { params: { path: { key: id } } }),
          ws.GET("/api/knowledge/{key}/history", { params: { path: { key: id } } }),
        ]);
        if (detail.response.status === 401 || hist.response.status === 401) {
          router.push("/workspace/login");
          return;
        }
        if (!detail.response.ok || !detail.data) {
          setDetailError(`entry not found (${detail.response.status})`);
          return;
        }
        setSelected(detail.data);
        if (hist.response.ok && hist.data) setHistory(hist.data.items);
      } catch (err) {
        setDetailError(err instanceof Error ? err.message : "entry load failed");
      }
    },
    [router],
  );

  const visible = useMemo(
    () =>
      hits.filter((h) => {
        if (kind !== "All" && h.kind !== kind) return false;
        if (verifiedOnly && !isVerified(h)) return false;
        return true;
      }),
    [hits, kind, verifiedOnly],
  );

  const kindCounts = useMemo(() => countKinds(hits), [hits]);

  return (
    <div className="mx-auto w-full max-w-6xl">
      <p className="font-mono text-[10px] uppercase tracking-widest text-cyan2">
        Institutional memory
      </p>
      <h1 className="mt-1 text-2xl font-semibold text-white">Knowledge</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted">
        Versioned project memory — rules, decisions, notes and snapshots. Approved
        decisions and successful patterns become reusable memory for future tasks.
      </p>

      <form
        onSubmit={(e) => void search(e)}
        data-knowledge-search
        className="mt-5 flex flex-wrap items-center gap-3 rounded-2xl border border-line bg-panel p-3"
      >
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask your workspace knowledge…"
          aria-label="Search knowledge"
          className="h-10 min-w-52 flex-1 rounded-xl border border-line2 bg-abyss px-4 text-sm text-fog outline-none placeholder:text-dim/70 focus:border-brand/60"
        />
        <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-line bg-abyss px-3 py-2 text-sm text-fog">
          <input
            type="checkbox"
            checked={verifiedOnly}
            onChange={(e) => setVerifiedOnly(e.target.checked)}
            aria-label="Verified only"
            className="accent-brand"
          />
          Verified only
        </label>
        <Button type="submit" variant="primary" disabled={busy || !query.trim()}>
          {busy ? "Searching…" : "Search"}
        </Button>
      </form>
      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}

      <div className="mt-5 grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside
          aria-label="Knowledge folders"
          className="h-fit rounded-2xl border border-line bg-panel p-3"
        >
          <h2 className="px-2 pb-1 text-[9px] font-semibold uppercase tracking-widest text-dim">
            Library
          </h2>
          {["All", ...KIND_OPTIONS].map((k) => {
            const count = k === "All" ? hits.length : (kindCounts[k as keyof typeof kindCounts] ?? 0);
            const active = kind === k;
            return (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                aria-pressed={active}
                className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] transition-colors ${
                  active
                    ? "border-l-2 border-brand bg-panel2 font-medium text-fog"
                    : "border-l-2 border-transparent text-dim hover:bg-panel2 hover:text-fog"
                }`}
              >
                <span className="flex-1 truncate text-left">
                  {k === "All" ? "All documents" : `${k[0].toUpperCase()}${k.slice(1)}s`}
                </span>
                <b className="font-mono text-[11px] font-normal">{count}</b>
              </button>
            );
          })}
          <p className="mt-3 px-2 font-mono text-[10px] leading-relaxed text-dim">
            {searched
              ? `showing ${hits.length} · ${visible.length} match filters`
              : "search to browse"}
          </p>
        </aside>

        <div>
          {!searched && busy ? (
            <p className="text-sm text-muted" aria-live="polite">
              Loading recent entries…
            </p>
          ) : null}
          {visible.length > 0 ? (
            <ul data-doc-grid className="grid gap-3 sm:grid-cols-2">
              {visible.map((hit) => {
                const version = citationVersion(hit.citation);
                return (
                  <li key={hit.citation}>
                    <button
                      type="button"
                      data-doc-card={hit.citation}
                      onClick={() => void open(hit)}
                      className="block h-full w-full rounded-2xl border border-line bg-panel p-4 text-left transition-colors hover:border-brand/60"
                    >
                      <div className="flex items-center gap-2">
                        <span className="rounded-full border border-line2 bg-panel2 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide text-cyan2">
                          {hit.kind}
                        </span>
                        {version ? (
                          <span
                            data-version
                            className="font-mono text-[11px] text-dim"
                          >
                            {version}
                          </span>
                        ) : null}
                        <span className="flex-1" />
                        <VerifiedPill verified={isVerified(hit)} />
                      </div>
                      <h3 className="mt-2 text-[15px] font-semibold leading-snug text-white">
                        {hit.title}
                      </h3>
                      {hit.summary ? (
                        <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-dim">
                          {hit.summary}
                        </p>
                      ) : null}
                      <p className="mt-3 border-t border-line pt-2 font-mono text-[11px] text-dim">
                        {hit.citation in usage
                          ? usageLine(usage[hit.citation])
                          : "Usage loading…"}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : searched && !busy && !error ? (
            <p className="rounded-2xl border border-line bg-panel p-6 text-center text-sm text-muted">
              No knowledge entries loaded yet — entries are {EMPTY_GUIDE}.
            </p>
          ) : null}
          {detailError ? <p className="mt-4 text-sm text-danger">{detailError}</p> : null}
          {selected ? (
            <article
              data-entry-detail
              className="mt-5 rounded-2xl border border-line bg-panel p-5"
            >
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <h2 className="min-w-0 flex-1 text-base font-semibold text-white">
                  {selected.title}
                </h2>
                <span className="font-mono text-[11px] text-dim">{selected.kind}</span>
                <span data-version className="font-mono text-[11px] text-dim">
                  v{selected.version}
                </span>
                <VerifiedPill verified={isVerified(selected)} />
              </div>
              <p className="mb-1 font-mono text-[11px] text-dim">{selected.citation}</p>
              <p className="mb-3 font-mono text-[11px] text-cyan2">
                {usageLine(
                  selected.citation in usage ? usage[selected.citation] : null,
                )}
              </p>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-fog">
                {selected.body}
              </p>
              {history.length > 1 ? (
                <section
                  aria-label="Version history"
                  className="mt-4 border-t border-line pt-3"
                >
                  <h3 className="mb-2 font-mono text-[10px] uppercase tracking-widest text-dim">
                    Version history
                  </h3>
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
        </div>
      </div>
    </div>
  );
}
