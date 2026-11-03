"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { csrfHeaders, parseSseFrames, pumpStream, ws } from "@/lib/api/workspace";

interface ChatResult {
  message_id: string;
  reply: string;
  question: string | null;
  slice: { id: number; title: string } | null;
  tasks: { id: number; title: string }[];
  created: number;
}

export default function ChatPage() {
  const router = useRouter();
  const [project, setProject] = useState("demo");
  const [message, setMessage] = useState("");
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<ChatResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim() || busy) return;
    setError("");
    setResult(null);
    setDraft("");
    setBusy(true);
    const outgoing = message;
    setMessage("");
    try {
      const { response } = await ws.POST("/api/chat", {
        parseAs: "stream",
        body: { message: outgoing, project: project.trim() || "demo" },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        return;
      }
      if (!response.ok || !response.body) {
        const detail = await response.text().catch(() => "");
        setError(detail || `chat failed (${response.status})`);
        return;
      }
      let all = "";
      let seen = "";
      await pumpStream(response, (text) => {
        all += text;
        const tokens = parseSseFrames(all)
          .filter((f) => f.event === "token")
          .map((f) => {
            try {
              return (JSON.parse(f.data) as { text?: string }).text ?? "";
            } catch {
              return "";
            }
          })
          .join("");
        if (tokens !== seen) {
          seen = tokens;
          setDraft(tokens);
        }
      });
      const frames = parseSseFrames(all);
      const errors = frames.filter((f) => f.event === "error");
      if (errors.length > 0) {
        setError(errors[0].data);
        return;
      }
      const finals = frames.filter((f) => f.event === "result");
      if (finals.length === 0) {
        setError("the model turn produced no result");
        return;
      }
      const parsed = JSON.parse(finals[finals.length - 1].data) as ChatResult;
      setResult(parsed);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "chat failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl">
      <h1 className="text-2xl font-semibold text-white">Supervisor chat</h1>
      <p className="mt-1 text-sm text-muted">
        One turn: either a single clarifying question or a slice with tasks. The reply carries
        inline task cards.
      </p>
      <form onSubmit={(e) => void send(e)} className="mt-6 space-y-3">
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Project
          </span>
          <input
            value={project}
            onChange={(e) => setProject(e.target.value)}
            className="h-10 w-full rounded-xl border border-steel/60 bg-abyss px-4 font-mono text-sm text-cloud outline-none focus:border-brand/60"
          />
        </label>
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Message
          </span>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            placeholder="e.g. Slice the demo onboarding into small tasks"
            className="w-full rounded-xl border border-steel/60 bg-abyss p-4 text-sm text-cloud outline-none placeholder:text-muted/60 focus:border-brand/60"
          />
        </label>
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        <Button type="submit" variant="primary" disabled={busy || !message.trim()}>
          {busy ? "Working…" : "Send"}
        </Button>
      </form>
      {busy || draft ? (
        <div className="mt-6 rounded-2xl border border-steel/40 bg-abyss/60 p-4">
          <p className="mb-2 font-mono text-xs uppercase tracking-wider text-muted">
            {busy ? "streaming" : "stream"}
          </p>
          <p className="whitespace-pre-wrap font-mono text-sm text-cloud">
            {draft}
            {busy ? <span className="animate-blink">_</span> : null}
          </p>
        </div>
      ) : null}
      {result ? (
        <div className="mt-6 space-y-4">
          {result.question ? (
            <div className="rounded-2xl border border-warning/35 bg-warning/5 p-4">
              <p className="mb-1 font-mono text-xs uppercase tracking-wider text-warning">
                clarifying question
              </p>
              <p className="text-sm text-cloud">{result.question}</p>
            </div>
          ) : (
            <div className="rounded-2xl border border-steel/40 bg-abyss/60 p-4">
              <p className="mb-3 text-sm text-cloud">{result.reply.split("\n")[0]}</p>
              <ul className="space-y-2">
                {result.slice ? (
                  <li key={`slice-${result.slice.id}`}>
                    <Link
                      href={`/workspace/tasks/${result.slice.id}`}
                      className="block rounded-xl border border-brand/40 bg-brand/5 p-3 text-sm text-cloud transition-colors hover:border-brand/70"
                    >
                      <span className="font-mono text-xs text-brand-300">
                        slice #{result.slice.id}
                      </span>{" "}
                      {result.slice.title}
                    </Link>
                  </li>
                ) : null}
                {result.tasks.map((t) => (
                  <li key={t.id}>
                    <Link
                      href={`/workspace/tasks/${t.id}`}
                      className="block rounded-xl border border-steel/30 bg-navy/60 p-3 text-sm text-cloud transition-colors hover:border-brand/50"
                    >
                      <span className="font-mono text-xs text-muted">#{t.id}</span> {t.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
