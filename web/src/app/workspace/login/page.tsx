"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { setCsrfToken, setSessionUsername, ws } from "@/lib/api/workspace";

// Standalone login: no workspace shell here (the layout skips its sidebar
// and topbar on this route). Official brand mark from rouvr.ai
// (public/rouvr-logo.svg, byte-identical to the marketing site icon)
// plus the "Rouvr." wordmark only — no brand copy.
export default function WorkspaceLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const { data, error: err, response } = await ws.POST("/api/auth/login", {
        body: { username: username.trim(), password, remember },
      });
      if (!response.ok || !data) {
        const detail =
          (err as { detail?: string } | undefined)?.detail ?? `login failed (${response.status})`;
        if (response.status === 401) router.prefetch("/workspace/login");
        setError(detail);
        return;
      }
      setCsrfToken(data.csrf_token);
      setSessionUsername(data.username);
      router.push("/workspace");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-sm flex-col items-center justify-center text-center">
      <img src="/rouvr-logo.svg" alt="" width={64} height={64} aria-hidden />
      <h1 className="sr-only">Sign in to Rouvr</h1>
      <p className="mt-4 text-xl font-semibold text-white">Rouvr.</p>
      <form onSubmit={submit} className="mt-8 w-full space-y-4 text-left">
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Username
          </span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 text-sm text-fog outline-none placeholder:text-dim/70 focus:border-brand/60"
            placeholder="admin"
          />
        </label>
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Password
          </span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 text-sm text-fog outline-none placeholder:text-dim/70 focus:border-brand/60"
            placeholder="••••••••••••"
          />
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-dim">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="accent-brand"
          />
          Remember me on this device (30 days)
        </label>
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        <Button type="submit" variant="primary" className="w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
