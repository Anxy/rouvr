"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { csrfHeaders, ws } from "@/lib/api/workspace";

type Props = {
  taskId: number;
  repoHint?: string | null;
  onGranted?: () => void;
};

/** Collect a push/PR PAT when the coder is awaiting_git_credential. */
export function GitGrantPanel({ taskId, repoHint, onGranted }: Props) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ok, setOk] = useState(false);

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const { response, error: err } = await ws.POST("/api/tasks/{id}/git-grant", {
        params: { path: { id: String(taskId) } },
        body: { github_token: token },
        headers: csrfHeaders(),
      });
      // Clear the field immediately so the secret does not linger in React state.
      setToken("");
      if (!response.ok) {
        const detail =
          err && typeof err === "object" && "detail" in err
            ? String((err as { detail: unknown }).detail)
            : `grant failed (${response.status})`;
        setError(detail);
        return;
      }
      setOk(true);
      onGranted?.();
    } catch (e) {
      setToken("");
      setError(e instanceof Error ? e.message : "grant failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      data-git-grant
      className="space-y-2 rounded-2xl border border-warning/40 bg-warning/10 px-4 py-3"
    >
      <h3 className="text-sm font-medium text-warning">Grant GitHub access</h3>
      <p className="text-[13px] text-fog">
        The coder committed locally and needs a fine-scoped personal access token
        to push and open a pull request
        {repoHint ? (
          <>
            {" "}
            on <span className="font-mono text-cloud">{repoHint}</span>
          </>
        ) : null}
        . The token is used only for push/PR, never shown to the coder agent, and
        is not stored in chat or evidence.
      </p>
      <label className="block text-xs font-mono uppercase tracking-wider text-dim">
        GitHub token
        <input
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ghp_…"
          className="mt-1 w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 font-sans text-sm text-cloud"
        />
      </label>
      {error ? <p className="text-sm text-danger">{error}</p> : null}
      {ok ? <p className="text-sm text-fog">Grant accepted — delivery will resume.</p> : null}
      <Button type="button" disabled={busy || !token.trim()} onClick={() => void submit()}>
        {busy ? "Saving…" : "Grant access"}
      </Button>
    </section>
  );
}
