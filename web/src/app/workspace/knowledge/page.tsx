"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ws, type KnowledgeEntry, type KnowledgeHit } from "@/lib/api/workspace";
import {
  DEFAULT_QUERY,
  KIND_OPTIONS,
  citationId,
  citationVersion,
  countKinds,
  isVerified,
} from "@/lib/knowledge";

// Knowledge browser. The ledger exposes search only (q is required; a blank
// query is a 422), so the page opens with a broad default search instead of
// an empty prompt. No project filter is sent: tenant scoping is enforced
// server-side, and scoping to the session's first project hid entries that
// live under other projects of the same workspace. Kind counts tally the
// loaded shortlist ("showing N"), never a ledger-wide total. Per-entry
// usage statistics are omitted: this page loads no task data to source
// them from, and such numbers must not be invented.

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

export default function KnowledgePage() {
  const router = useRouter();
  const [query, setQuery] = useState(DEFAULT_QUERY);
  const [kind, setKind] = useState("All");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<KnowledgeEntry | null>(null);
  const [history, setHistory] = useState<KnowledgeEntry[]>([]);
  const [detailError, setDetailError] = useState("");
  const defaultRan = useRef(false);

  const search = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      if (!query.trim() || busy) return;
      setBusy(true);
      setError("");
      setSelected(null);
      setHistory([]);
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
      } catch (err) {
        setError(err instanceof Error ? err.message : "knowledge search failed");
        setHits([]);
      } finally {
        setBusy(false);
      }
    },
    [query, busy, router],
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
    <div className="mx-auto w-full max-w-3xl">
      <h1 className="text-2xl font-semibold text-white">Knowledge</h1>
      <section
        data-explainer
        aria-label="What the knowledge system is"
        className="mt-3 rounded-2xl border border-steel/40 bg-abyss/60 p-4"
      >
        <p className="text-sm text-cloud">
          The knowledge system keeps what the workspace has learned. Entries are
          created from approved decisions, task outcomes and chat, and a human
          confirms rules before they count as verified.
        </p>
      </section>
      <form onSubmit={(e) => void search(e)} className="mt-6 space-y-3">
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Search
          </span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. deploy policy"
            aria-label="Search knowledge"
            className="h-10 w-full rounded-xl border border-steel/60 bg-abyss px-4 text-sm text-cloud outline-none placeholder:text-muted/60 focus:border-brand/60"
          />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            aria-label="Filter by kind"
            className="rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud focus:border-brand/60 focus:outline-none"
          >
            <option value="All">All kinds</option>
            {KIND_OPTIONS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud">
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
        </div>
      </form>
      {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}
      {searched && !error && hits.length > 0 ? (
        <p
          data-kind-counts
          className="mt-4 font-mono text-xs text-muted"
          aria-live="polite"
        >
          showing {hits.length}
          {KIND_OPTIONS.map((k) => ` · ${k}s ${kindCounts[k]}`).join("")} ·{" "}
          {visible.length} of {hits.length} match filters
        </p>
      ) : null}
      {!searched && busy ? (
        <p className="mt-3 text-sm text-muted" aria-live="polite">
          Loading recent entries…
        </p>
      ) : null}
      {visible.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {visible.map((hit) => {
            const version = citationVersion(hit.citation);
            return (
              <li key={hit.citation}>
                <button
                  type="button"
                  onClick={() => void open(hit)}
                  className="block w-full rounded-xl border border-steel/30 bg-navy/60 px-3 py-2 text-left transition-colors hover:border-brand/50"
                >
                  <div className="flex items-center gap-3">
                    <p className="min-w-0 flex-1 truncate text-sm text-cloud">{hit.title}</p>
                    <span className="hidden shrink-0 font-mono text-[11px] text-muted sm:inline">
                      {hit.kind}
                    </span>
                    {version ? (
                      <span
                        data-version
                        className="shrink-0 font-mono text-[11px] text-muted"
                      >
                        {version}
                      </span>
                    ) : null}
                    <VerifiedPill verified={isVerified(hit)} />
                  </div>
                  <p className="mt-1 truncate font-mono text-[11px] text-muted">
                    {hit.citation}
                  </p>
                  {hit.summary ? (
                    <p className="mt-1 truncate text-xs text-muted">{hit.summary}</p>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : searched && !busy && !error ? (
        <p className="mt-3 rounded-2xl border border-steel/40 bg-abyss/60 p-6 text-center text-sm text-muted">
          No knowledge entries loaded yet — entries are {EMPTY_GUIDE}.
        </p>
      ) : null}
      {detailError ? <p className="mt-4 text-sm text-danger">{detailError}</p> : null}
      {selected ? (
        <article className="mt-6 rounded-2xl border border-steel/40 bg-abyss/60 p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h2 className="min-w-0 flex-1 text-sm font-semibold text-white">
              {selected.title}
            </h2>
            <span className="font-mono text-[11px] text-muted">{selected.kind}</span>
            <span data-version className="font-mono text-[11px] text-muted">
              v{selected.version}
            </span>
            <VerifiedPill verified={isVerified(selected)} />
          </div>
          <p className="mb-3 font-mono text-[11px] text-muted">{selected.citation}</p>
          <p className="whitespace-pre-wrap text-sm text-cloud">{selected.body}</p>
          {history.length > 1 ? (
            <section aria-label="Version history" className="mt-4 border-t border-steel/30 pt-3">
              <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
                Version history
              </h3>
              <ol className="space-y-1.5">
                {history.map((v) => (
                  <li
                    key={v.citation}
                    className="flex items-center gap-2 font-mono text-xs text-muted"
                  >
                    <span className="text-cloud">{v.citation}</span>
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
  );
}
