"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/workspace/session";
import { csrfHeaders, ws } from "@/lib/api/workspace";

type Step = 1 | 2 | 3;

function detailMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

function slugify(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

/** First-run wizard: project → GitHub repo → optional PAT → Supervisor. */
export default function OnboardingPage() {
  const router = useRouter();
  const { status, tenant, projects, setProject, refresh } = useSession();
  const [step, setStep] = useState<Step>(1);
  const [projectName, setProjectName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [repo, setRepo] = useState("");
  const [baseBranch, setBaseBranch] = useState("main");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Suppress the "already has projects → leave" redirect once the wizard
  // creates its first project (refresh would otherwise bounce to Board).
  const wizardStarted = useRef(false);

  useEffect(() => {
    if (status === "anonymous") {
      router.replace("/workspace/login");
      return;
    }
    if (
      status === "authenticated" &&
      projects.length > 0 &&
      !wizardStarted.current &&
      step === 1
    ) {
      router.replace("/workspace");
    }
  }, [status, projects.length, router, step]);

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    const name = slugify(projectName) || slugify(title);
    if (!name) {
      setError("Project name is required");
      return;
    }
    if (!tenant) {
      setError("Session tenant missing — sign in again");
      return;
    }
    setBusy(true);
    setError("");
    wizardStarted.current = true;
    try {
      const { data, response, error: err } = await ws.POST("/api/projects", {
        body: {
          tenant,
          project: name,
          ...(title.trim() ? { title: title.trim() } : {}),
          ...(description.trim() ? { description: description.trim() } : {}),
        },
        headers: csrfHeaders(),
      });
      if (!response.ok || !data) {
        wizardStarted.current = false;
        setError(detailMessage(err, `Could not create project (${response.status})`));
        return;
      }
      setProjectName(data.project);
      setProject(data.project);
      await refresh();
      setStep(2);
    } catch (err) {
      wizardStarted.current = false;
      setError(err instanceof Error ? err.message : "create failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveRepo(e: React.FormEvent) {
    e.preventDefault();
    const name = projectName.trim();
    if (!name) {
      setError("Create a project first");
      return;
    }
    if (!repo.trim() || !repo.includes("/")) {
      setError("Repo must look like owner/name");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const { response, error: err } = await ws.PUT("/api/projects/{name}/exec-spec", {
        params: { path: { name } },
        body: {
          tenant: tenant ?? undefined,
          repo: repo.trim(),
          base_branch: baseBranch.trim() || "main",
        },
        headers: csrfHeaders(),
      });
      if (!response.ok) {
        setError(detailMessage(err, `Could not save repo (${response.status})`));
        return;
      }
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setBusy(false);
    }
  }

  async function saveGrantOrSkip(skip: boolean) {
    if (!skip && !token.trim()) {
      setError("Paste a token, or skip for now");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (!skip) {
        const name = projectName.trim();
        const { response, error: err } = await ws.POST("/api/projects/{name}/git-grant", {
          params: { path: { name } },
          body: {
            github_token: token,
            ...(tenant ? { tenant } : {}),
          },
          headers: csrfHeaders(),
        });
        setToken("");
        if (!response.ok) {
          setError(detailMessage(err, `Could not save grant (${response.status})`));
          return;
        }
      }
      router.push("/workspace/chat?onboarded=1");
    } catch (err) {
      setToken("");
      setError(err instanceof Error ? err.message : "grant failed");
    } finally {
      setBusy(false);
    }
  }

  if (status === "loading" || status === "anonymous") {
    return (
      <div className="mx-auto flex min-h-[70vh] max-w-md items-center justify-center text-sm text-dim">
        {status === "loading" ? "Loading…" : "Redirecting…"}
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-md flex-col justify-center">
      <img src="/rouvr-logo.svg" alt="" width={56} height={56} aria-hidden className="mx-auto" />
      <p className="mt-3 text-center text-xl font-semibold tracking-tight text-white">
        rouvr<span className="text-brand">.</span>
      </p>
      <h1 className="mt-6 text-center text-2xl font-semibold text-white">Set up your workspace</h1>
      <p className="mt-2 text-center text-sm text-dim">
        Three quick steps, then talk to the supervisor to ship work.
      </p>

      <ol className="mt-6 flex items-center justify-center gap-2" aria-label="Progress">
        {([1, 2, 3] as Step[]).map((n) => (
          <li
            key={n}
            className={`h-2 w-8 rounded-full ${n <= step ? "bg-brand" : "bg-steel/40"}`}
            aria-current={n === step ? "step" : undefined}
          />
        ))}
      </ol>
      <p className="mt-2 text-center font-mono text-[11px] uppercase tracking-wider text-muted">
        Step {step} of 3
      </p>

      {step === 1 ? (
        <form onSubmit={createProject} className="mt-8 space-y-4">
          <p className="font-mono text-xs uppercase tracking-wider text-cyan2">Workspace</p>
          {tenant ? (
            <p className="text-sm text-dim">
              Tenant <span className="font-mono text-cloud">{tenant}</span>
            </p>
          ) : null}
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
              Project name
            </span>
            <input
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 text-sm text-fog outline-none focus:border-brand/60"
              placeholder="site"
              autoFocus
              required
            />
          </label>
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
              Title
            </span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 text-sm text-fog outline-none focus:border-brand/60"
              placeholder="Marketing site"
            />
          </label>
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
              Description (optional)
            </span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-xl border border-line2 bg-panel px-4 py-3 text-sm text-fog outline-none focus:border-brand/60"
              placeholder="What this project is for"
            />
          </label>
          {error ? <p className="text-sm text-danger">{error}</p> : null}
          <Button type="submit" variant="primary" className="w-full" disabled={busy}>
            {busy ? "Creating…" : "Continue"}
          </Button>
        </form>
      ) : null}

      {step === 2 ? (
        <form onSubmit={saveRepo} className="mt-8 space-y-4">
          <p className="font-mono text-xs uppercase tracking-wider text-cyan2">GitHub repo</p>
          <p className="text-sm text-dim">
            Coding delivery clones this repo. You can confirm again when you start a task.
          </p>
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
              owner/name
            </span>
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 font-mono text-sm text-fog outline-none focus:border-brand/60"
              placeholder="acme/site"
              autoFocus
              required
            />
          </label>
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
              Base branch
            </span>
            <input
              value={baseBranch}
              onChange={(e) => setBaseBranch(e.target.value)}
              className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 font-mono text-sm text-fog outline-none focus:border-brand/60"
              placeholder="main"
            />
          </label>
          {error ? <p className="text-sm text-danger">{error}</p> : null}
          <Button type="submit" variant="primary" className="w-full" disabled={busy}>
            {busy ? "Saving…" : "Continue"}
          </Button>
        </form>
      ) : null}

      {step === 3 ? (
        <div className="mt-8 space-y-4">
          <p className="font-mono text-xs uppercase tracking-wider text-cyan2">GitHub access</p>
          <p className="text-sm text-dim">
            Optional now. A fine-scoped personal access token lets the coder push and open a
            pull request later. It is never shown to the agent. You can also grant per-task when
            delivery asks.
          </p>
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
              GitHub token
            </span>
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="h-11 w-full rounded-xl border border-line2 bg-panel px-4 font-mono text-sm text-fog outline-none focus:border-brand/60"
              placeholder="ghp_…"
              autoFocus
            />
          </label>
          {error ? <p className="text-sm text-danger">{error}</p> : null}
          <Button
            type="button"
            variant="primary"
            className="w-full"
            disabled={busy || !token.trim()}
            onClick={() => void saveGrantOrSkip(false)}
          >
            {busy ? "Saving…" : "Save token & open Supervisor"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-full"
            disabled={busy}
            onClick={() => void saveGrantOrSkip(true)}
          >
            Skip for now
          </Button>
        </div>
      ) : null}
    </div>
  );
}
