"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import {
  csrfHeaders,
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

// Backfilled entries often repeat the summary as the body's first line
// (e.g. knowledge:1109) — render it once instead of twice.
function dedupedBody(summary: string | null, body: string): string {
  const s = (summary ?? "").trim();
  if (!s) return body;
  if (body.trim() === s) return "";
  if (body.startsWith(s)) return body.slice(s.length).replace(/^\s+/, "");
  return body;
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
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showNewVersion, setShowNewVersion] = useState(false);
  const [nvTitle, setNvTitle] = useState("");
  const [nvBody, setNvBody] = useState("");
  const [nvSummary, setNvSummary] = useState("");

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

  async function verify() {
    if (!key) return;
    setActionError("");
    setBusy(true);
    try {
      const { response } = await ws.POST("/api/knowledge/{key}/confirm", {
        params: { path: { key } },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setActionError(detail || `verify failed (${response.status})`);
        return;
      }
      await load(key);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "verify failed");
    } finally {
      setBusy(false);
    }
  }

  async function createVersion(e?: React.FormEvent) {
    e?.preventDefault();
    if (!key || !nvTitle.trim() || !nvBody.trim()) return;
    setActionError("");
    setBusy(true);
    try {
      const { response } = await ws.POST("/api/knowledge/{key}/versions", {
        params: { path: { key } },
        body: {
          title: nvTitle.trim(),
          body: nvBody,
          ...(nvSummary.trim() ? { summary: nvSummary.trim() } : {}),
        },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        setActionError(detail || `new version failed (${response.status})`);
        return;
      }
      setShowNewVersion(false);
      setNvSummary("");
      await load(key);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "new version failed");
    } finally {
      setBusy(false);
    }
  }

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
          <div className="mt-3 flex flex-wrap gap-2">
            {!isVerified(entry) ? (
              <Button
                type="button"
                variant="secondary"
                data-verify-entry
                disabled={busy}
                onClick={() => void verify()}
              >
                {busy ? "Working…" : "Verify"}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              data-new-version
              disabled={busy}
              onClick={() => {
                setActionError("");
                setNvTitle(entry.title);
                setNvBody(entry.body);
                setShowNewVersion((v) => !v);
              }}
            >
              {showNewVersion ? "Close" : "New version"}
            </Button>
          </div>
          {actionError ? <p className="mt-2 text-sm text-danger">{actionError}</p> : null}
          {showNewVersion ? (
            <form
              onSubmit={(e) => void createVersion(e)}
              className="mt-3 space-y-3 rounded-xl border border-line bg-abyss/60 p-4"
            >
              <p className="text-[13px] text-dim">
                A new version supersedes this entry — history stays append-only.
              </p>
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Title
                </span>
                <input
                  value={nvTitle}
                  onChange={(e) => setNvTitle(e.target.value)}
                  aria-label="New version title"
                  className="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud focus:border-brand/60 focus:outline-none"
                />
              </label>
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Body
                </span>
                <textarea
                  value={nvBody}
                  onChange={(e) => setNvBody(e.target.value)}
                  rows={6}
                  aria-label="New version body"
                  className="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud focus:border-brand/60 focus:outline-none"
                />
              </label>
              <label className="block">
                <span className="mb-1 block font-mono text-[11px] uppercase tracking-wider text-muted">
                  Summary (optional — follows the newest body when blank)
                </span>
                <input
                  value={nvSummary}
                  onChange={(e) => setNvSummary(e.target.value)}
                  placeholder="leave blank to derive from the body"
                  aria-label="New version summary"
                  className="w-full rounded-xl border border-steel/40 bg-navy/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
                />
              </label>
              <Button
                type="submit"
                disabled={busy || !nvTitle.trim() || !nvBody.trim()}
              >
                {busy ? "Saving…" : "Save new version"}
              </Button>
            </form>
          ) : null}
          {entry.summary ? (
            <p className="mt-3 text-sm leading-relaxed text-dim">{entry.summary}</p>
          ) : null}
          {(() => {
            const body = dedupedBody(entry.summary, entry.body);
            return body ? (
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-fog">
                {body}
              </p>
            ) : null;
          })()}
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
