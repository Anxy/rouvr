"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { csrfHeaders, ws } from "@/lib/api/workspace";

export type PlanStartTarget = {
  slug: string;
  title: string;
  tenant: string;
  project: string;
  /** True = clear phase cursors and rebuild from coder. */
  restart?: boolean;
};

type Props = {
  open: boolean;
  plan: PlanStartTarget;
  onClose: () => void;
  onDone?: (results: { id: number; ok: boolean; detail: string }[]) => void;
};

function detailMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

/** Confirm GitHub repo (+ optional PAT), then start the whole plan chain. */
export function DeliveryStartDialog({ open, plan, onClose, onDone }: Props) {
  const [repo, setRepo] = useState("");
  const [baseBranch, setBaseBranch] = useState("main");
  const [pat, setPat] = useState("");
  const [hasGrant, setHasGrant] = useState(false);
  const [repoEmpty, setRepoEmpty] = useState(false);
  const [warning, setWarning] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!open || !plan) return;
    let cancelled = false;
    void (async () => {
      const { data, response } = await ws.GET("/api/projects/{name}/exec-spec", {
        params: {
          path: { name: plan.project },
          query: { tenant: plan.tenant },
        },
      });
      if (cancelled || !response.ok || !data) return;
      setRepo(data.repo ?? "");
      setBaseBranch(data.base_branch || "main");
      setHasGrant(Boolean(data.has_git_grant));
      setRepoEmpty(Boolean(data.repo_empty));
      if (data.warning) setWarning(data.warning);
    })();
    return () => {
      cancelled = true;
    };
  }, [open, plan]);

  if (!open || !plan) return null;

  async function run() {
    setBusy(true);
    setError("");
    setWarning("");
    setStatus("");
    try {
      const save = await ws.PUT("/api/projects/{name}/exec-spec", {
        params: { path: { name: plan.project } },
        body: {
          tenant: plan.tenant,
          repo: repo.trim(),
          base_branch: baseBranch.trim() || "main",
        },
        headers: csrfHeaders(),
      });
      if (!save.response.ok) {
        setError(detailMessage(save.error, `Could not save repo (${save.response.status})`));
        return;
      }
      const empty = Boolean(save.data?.repo_empty);
      const granted = Boolean(save.data?.has_git_grant) || hasGrant;
      setRepoEmpty(empty);
      setHasGrant(granted);
      if (save.data?.warning) setWarning(save.data.warning);

      const patValue = pat.trim();
      // Hard stop: no PAT and no saved grant → show error and keep modal open.
      // (Previously we only warned and continued; a successful start then closed.)
      if (!patValue && !granted) {
        setError(
          empty
            ? "Paste a GitHub PAT (repo scope) so Rouvr can bootstrap the empty repo and push/PR."
            : "Paste a GitHub PAT (repo scope) to push branches and open PRs, or save one during onboarding first.",
        );
        return;
      }

      if (patValue) {
        const grant = await ws.POST("/api/projects/{name}/git-grant", {
          params: { path: { name: plan.project } },
          body: { tenant: plan.tenant, github_token: patValue },
          headers: csrfHeaders(),
        });
        setPat("");
        if (!grant.response.ok) {
          setError(detailMessage(grant.error, `Could not save git grant (${grant.response.status})`));
          return;
        }
        setHasGrant(true);
      }

      const restart = Boolean(plan.restart);
      setStatus(restart ? "Restarting plan chain from build…" : "Starting plan chain…");
      const { data, error: startErr, response } = await ws.POST("/api/plans/{slug}/start", {
        params: { path: { slug: plan.slug } },
        body: restart ? { restart: true } : {},
        headers: csrfHeaders(),
      });
      if (!response.ok || !data) {
        setError(detailMessage(startErr, `start failed (${response.status})`));
        setStatus("");
        return;
      }
      const results = (data.results ?? []).map((r) => ({
        id: r.task_id,
        ok: r.ok,
        detail: r.detail || r.workflow_id || (r.ok ? "started" : "failed"),
      }));
      const started = data.started ?? results.filter((r) => r.ok).length;
      const total = data.total ?? results.length;
      const allOk = total > 0 && started === total && results.every((r) => r.ok);

      if (!allOk) {
        const failed = results.filter((r) => !r.ok);
        setStatus(`Started ${started}/${total} chain task(s)`);
        setError(
          failed.length > 0
            ? failed.map((r) => `#${r.id}: ${r.detail}`).join(" · ")
            : `Start incomplete (${started}/${total})`,
        );
        return;
      }

      setStatus(`Started ${started}/${total} chain task(s)`);
      onDone?.(results);
      onClose();
    } catch (err) {
      setStatus("");
      setError(err instanceof Error ? err.message : "start failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delivery-start-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <form
        className="w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!busy && repo.trim()) void run();
        }}
      >
        <h2 id="delivery-start-title" className="text-lg font-semibold text-white">
          {plan.restart ? "Restart from build" : "Start delivery"}
        </h2>
        <p className="mt-1 text-sm text-muted">
          {plan.restart
            ? "Clears phase checkpoints and rebuilds every startable chain task from coder."
            : "Resumes each startable chain task from its last checkpoint (build → test → verify → close)."}{" "}
          Plan: <span className="text-fog">{plan.title}</span>. Confirm the GitHub repository first.
        </p>
        <p className="mt-2 font-mono text-[11px] text-dim">{plan.slug}</p>
        <label className="mt-4 block text-xs font-mono uppercase tracking-wider text-dim">
          Repository (owner/name)
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="acme/my-app"
            className="mt-1 w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 font-sans text-sm text-cloud"
            autoFocus
          />
        </label>
        <label className="mt-3 block text-xs font-mono uppercase tracking-wider text-dim">
          Base branch
          <input
            value={baseBranch}
            onChange={(e) => setBaseBranch(e.target.value)}
            placeholder="main"
            className="mt-1 w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 font-sans text-sm text-cloud"
          />
        </label>
        <label className="mt-3 block text-xs font-mono uppercase tracking-wider text-dim">
          GitHub PAT{hasGrant && !pat.trim() ? " (saved grant on file)" : ""}
          <input
            type="password"
            value={pat}
            onChange={(e) => setPat(e.target.value)}
            placeholder={
              hasGrant
                ? "optional — leave blank to reuse saved grant"
                : "ghp_… required (repo scope)"
            }
            autoComplete="off"
            className="mt-1 w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 font-sans text-sm text-cloud"
          />
        </label>
        {repoEmpty ? (
          <p className="mt-2 text-xs text-warning">
            Empty GitHub repo — a PAT is required so Rouvr can create the base branch.
          </p>
        ) : null}
        {warning ? <p className="mt-3 text-sm text-warning">{warning}</p> : null}
        {status ? <p className="mt-3 font-mono text-xs text-fog">{status}</p> : null}
        {error ? (
          <p className="mt-3 text-sm text-danger" role="alert">
            {error}
          </p>
        ) : null}
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || !repo.trim()}>
            {busy
              ? plan.restart
                ? "Restarting…"
                : "Starting…"
              : plan.restart
                ? "Restart plan from build"
                : "Start plan delivery"}
          </Button>
        </div>
      </form>
    </div>
  );
}

/** @deprecated Use PlanStartTarget — kept for transitional imports. */
export type StartTarget = PlanStartTarget & { id?: number };
