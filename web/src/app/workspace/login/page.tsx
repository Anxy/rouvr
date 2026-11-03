"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { setCsrfToken, ws } from "@/lib/api/workspace";

export default function WorkspaceLoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const { data, error: err, response } = await ws.POST("/api/auth/login", {
        body: { username: username.trim(), password },
      });
      if (!response.ok || !data) {
        const detail =
          (err as { detail?: string } | undefined)?.detail ?? `login failed (${response.status})`;
        if (response.status === 401) router.prefetch("/workspace/login");
        setError(detail);
        return;
      }
      setCsrfToken(data.csrf_token);
      router.push("/workspace");
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-md">
      <h1 className="text-2xl font-semibold text-white">Sign in to Rouvr</h1>
      <p className="mt-2 text-sm text-muted">
        Local account on this ledger. The session cookie is httponly; state-changing calls send
        the CSRF token issued at login.
      </p>
      <form onSubmit={submit} className="mt-6 space-y-4">
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Username
          </span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            className="h-11 w-full rounded-xl border border-steel/60 bg-abyss px-4 text-sm text-cloud outline-none placeholder:text-muted/60 focus:border-brand/60"
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
            className="h-11 w-full rounded-xl border border-steel/60 bg-abyss px-4 text-sm text-cloud outline-none placeholder:text-muted/60 focus:border-brand/60"
            placeholder="••••••••••••"
          />
        </label>
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        <Button type="submit" variant="primary" className="w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
