"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import {
  csrfHeaders,
  ws,
  type KnowledgeHit,
  type KnowledgeUsage,
} from "@/lib/api/workspace";
import {
  DEFAULT_QUERY,
  KIND_OPTIONS,
  citationId,
  citationVersion,
  countKinds,
  isPinned,
  isVerified,
  reviewStateLabel,
} from "@/lib/knowledge";

// Knowledge browser in the Figma Knowledge shape: a search panel up top,
// kind folders aside, and clickable doc cards in a grid. Every number and
// list comes from the ledger. Per-row usage (applied-task count + last use)
// loads lazily per visible shortlist row; a row that cannot source usage
// renders an em dash, never an invented number. Opening a card navigates
// to the entry's own inner page (full body plus version history).

const PAGE_SIZE = "50";

// Client-side page slice for the doc grid: 10 cards per page with
// prev/next. Per-row usage metadata (G4) loads once per entity — only
// the rows on the current page fetch, merging into the cache below.
const PAGE_SIZE_CLIENT = 10;

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

function ReviewBadge({ state }: { state?: string }) {
  if (state !== "rejected" && state !== "needs_revision") return null;
  return (
    <span
      data-review-state={state}
      className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] ${
        state === "rejected"
          ? "border border-danger/40 bg-danger/10 text-danger"
          : "border border-warning/40 bg-warning/10 text-warning"
      }`}
    >
      {reviewStateLabel(state)}
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
  const [page, setPage] = useState(1);
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [usage, setUsage] = useState<Record<string, KnowledgeUsage | null>>({});
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  // K4: hide entries with known zero usage. Rows whose usage is still
  // loading stay visible — only a loaded zero count hides a row.
  const [hideUnused, setHideUnused] = useState(false);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [pinning, setPinning] = useState<string | null>(null);
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
      setUsage((prev) => ({ ...prev, ...next }));
    },
    [router],
  );

  const search = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();
      if (!query.trim() || busy) return;
      setBusy(true);
      setError("");
      setUsage({});
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
        setPage(1);
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

  // Archived rows never reach the shortlist — the ledger excludes them
  // from search server-side, so no client filter is needed here.
  const visible = useMemo(
    () =>
      hits.filter((h) => {
        if (kind !== "All" && h.kind !== kind) return false;
        if (verifiedOnly && !isVerified(h)) return false;
        if (hideUnused) {
          const u = usage[h.citation];
          if (u && u.applied_to_tasks === 0) return false;
        }
        return true;
      }),
    [hits, kind, verifiedOnly, hideUnused, usage],
  );

  // Client-side 10/page slice; the page clamps to the filtered length so
  // narrowing the filters can never strand the grid on an empty page.
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE_CLIENT));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const pageHits = visible.slice(
    (safePage - 1) * PAGE_SIZE_CLIENT,
    safePage * PAGE_SIZE_CLIENT,
  );

  // G4: usage metadata loads once per entity — only rows on the current
  // page fetch, merging into the cache so revisiting a page refetches
  // nothing.
  useEffect(() => {
    const missing = pageHits.filter((h) => !(h.citation in usage));
    if (missing.length > 0) void loadUsage(missing);
  });

  const kindCounts = useMemo(() => countKinds(hits), [hits]);

  // K7: dashboard counts tally the loaded shortlist only — verified share
  // plus the kind folders aside. Nothing here claims a ledger-wide total.
  const verifiedCount = useMemo(() => hits.filter(isVerified).length, [hits]);

  async function reviewHit(hit: KnowledgeHit, verdict: "rejected" | "needs_revision") {
    const id = citationId(hit.citation);
    setReviewing(`${hit.citation}:${verdict}`);
    setError("");
    try {
      const { response } = await ws.POST("/api/knowledge/{key}/review", {
        params: { path: { key: id } },
        body: { verdict },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setError(detail || `review failed (${response.status})`);
        return;
      }
      await search();
    } catch (err) {
      setError(err instanceof Error ? err.message : "review failed");
    } finally {
      setReviewing(null);
    }
  }

  async function togglePin(hit: KnowledgeHit) {
    const id = citationId(hit.citation);
    setPinning(hit.citation);
    setError("");
    try {
      const { response } = await ws.POST("/api/knowledge/{key}/pin", {
        params: { path: { key: id } },
        body: { pinned: !isPinned(hit) },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setError(detail || `pin failed (${response.status})`);
        return;
      }
      // Pinned rows earn the same rank vote as verified rows, so the
      // reload re-sorts them to the top server-side.
      await search();
    } catch (err) {
      setError(err instanceof Error ? err.message : "pin failed");
    } finally {
      setPinning(null);
    }
  }

  async function verifyHit(hit: KnowledgeHit) {
    const id = citationId(hit.citation);
    setVerifying(hit.citation);
    setError("");
    try {
      const { response } = await ws.POST("/api/knowledge/{key}/review", {
        params: { path: { key: id } },
        body: { verdict: "verified" },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setError(detail || `verify failed (${response.status})`);
        return;
      }
      await search();
    } catch (err) {
      setError(err instanceof Error ? err.message : "verify failed");
    } finally {
      setVerifying(null);
    }
  }

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
      <p data-knowledge-counts className="mt-2 font-mono text-[11px] text-dim">
        {searched
          ? `showing ${hits.length} · ${verifiedCount} verified · ${visible.length} match filters`
          : "search to browse"}
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
            onChange={(e) => {
              setVerifiedOnly(e.target.checked);
              setPage(1);
            }}
            aria-label="Verified only"
            className="accent-brand"
          />
          Verified only
        </label>
        <label
          className="flex cursor-pointer items-center gap-2 rounded-xl border border-line bg-abyss px-3 py-2 text-sm text-fog"
          title="Hide entries with no applied tasks (entries still loading usage stay visible)"
        >
          <input
            type="checkbox"
            checked={hideUnused}
            onChange={(e) => {
              setHideUnused(e.target.checked);
              setPage(1);
            }}
            aria-label="Hide unused entries"
            className="accent-brand"
          />
          Hide unused
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
                onClick={() => {
              setKind(k);
              setPage(1);
            }}
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
            <>
              <ul data-doc-grid className="grid gap-3 sm:grid-cols-2">
                {pageHits.map((hit) => {
                const version = citationVersion(hit.citation);
                return (
                  <li key={hit.citation} data-doc-card={hit.citation}>
                    <article className="flex h-full flex-col rounded-2xl border border-line bg-panel p-4 transition-colors hover:border-brand/60">
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
                        <ReviewBadge state={hit.review_state} />
                        <VerifiedPill verified={isVerified(hit)} />
                        {isPinned(hit) ? (
                          <span
                            data-pinned={hit.citation}
                            className="shrink-0 rounded-full border border-brand/40 bg-brand/10 px-2 py-0.5 font-mono text-[11px] text-brand-300"
                          >
                            pinned
                          </span>
                        ) : null}
                      </div>
                      <Link
                        href={`/workspace/knowledge/${citationId(hit.citation)}`}
                        className="mt-2 block"
                      >
                        <h3 className="text-[15px] font-semibold leading-snug text-white hover:underline">
                          {hit.title}
                        </h3>
                        {hit.summary ? (
                          <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-dim">
                            {hit.summary}
                          </p>
                        ) : null}
                      </Link>
                      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-2">
                        <p className="min-w-0 flex-1 basis-full font-mono text-[11px] text-dim">
                          {hit.citation in usage
                            ? usageLine(usage[hit.citation])
                            : "Usage loading…"}
                        </p>
                        {!isVerified(hit) ? (
                          <button
                            type="button"
                            data-verify={hit.citation}
                            disabled={verifying === hit.citation}
                            onClick={() => void verifyHit(hit)}
                            className="shrink-0 rounded-lg border border-brand/40 bg-brand/10 px-2 py-1 font-mono text-[11px] text-brand-300 transition-colors hover:border-brand/70 disabled:opacity-50"
                          >
                            {verifying === hit.citation ? "Verifying…" : "Verify"}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          data-review-reject={hit.citation}
                          disabled={reviewing === `${hit.citation}:rejected`}
                          onClick={() => void reviewHit(hit, "rejected")}
                          className="shrink-0 rounded-lg border border-danger/40 bg-danger/10 px-2 py-1 font-mono text-[11px] text-danger transition-colors hover:border-danger/70 disabled:opacity-50"
                        >
                          {reviewing === `${hit.citation}:rejected` ? "Rejecting…" : "Reject"}
                        </button>
                        <button
                          type="button"
                          data-review-needs-revision={hit.citation}
                          disabled={reviewing === `${hit.citation}:needs_revision`}
                          onClick={() => void reviewHit(hit, "needs_revision")}
                          className="shrink-0 rounded-lg border border-warning/40 bg-warning/10 px-2 py-1 font-mono text-[11px] text-warning transition-colors hover:border-warning/70 disabled:opacity-50"
                        >
                          {reviewing === `${hit.citation}:needs_revision`
                            ? "Saving…"
                            : "Needs revision"}
                        </button>
                        <button
                          type="button"
                          data-pin={hit.citation}
                          aria-pressed={isPinned(hit)}
                          disabled={pinning === hit.citation}
                          onClick={() => void togglePin(hit)}
                          className="shrink-0 rounded-lg border border-line2 bg-abyss px-2 py-1 font-mono text-[11px] text-dim transition-colors hover:border-brand/60 hover:text-fog disabled:opacity-50"
                        >
                          {pinning === hit.citation
                            ? "Pinning…"
                            : isPinned(hit)
                              ? "Unpin"
                              : "Pin"}
                        </button>
                      </div>
                    </article>
                  </li>
                );
                })}
              </ul>
              {totalPages > 1 ? (
                <div
                  className="mt-4 flex items-center gap-2"
                  role="group"
                  aria-label="Knowledge pages"
                >
                  <button
                    type="button"
                    onClick={() => setPage(safePage - 1)}
                    disabled={safePage <= 1}
                    aria-label="Knowledge: previous page"
                    className="rounded-lg border border-line bg-abyss px-2.5 py-1 font-mono text-[11px] text-dim transition-colors hover:border-brand/60 hover:text-fog disabled:opacity-40"
                  >
                    Prev
                  </button>
                  <span className="font-mono text-[11px] text-dim">
                    Showing {(safePage - 1) * PAGE_SIZE_CLIENT + 1}–
                    {Math.min(safePage * PAGE_SIZE_CLIENT, visible.length)} of{" "}
                    {visible.length}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPage(safePage + 1)}
                    disabled={safePage >= totalPages}
                    aria-label="Knowledge: next page"
                    className="rounded-lg border border-line bg-abyss px-2.5 py-1 font-mono text-[11px] text-dim transition-colors hover:border-brand/60 hover:text-fog disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              ) : null}
            </>
          ) : searched && !busy && !error ? (
            <p className="rounded-2xl border border-line bg-panel p-6 text-center text-sm text-muted">
              No knowledge entries loaded yet — entries are {EMPTY_GUIDE}.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
