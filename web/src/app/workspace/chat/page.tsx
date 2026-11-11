"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { Combobox } from "@/components/ui/Select";
import { TaskStatePill } from "@/components/ui/StatusPill";
import { PipelineStrip } from "@/components/workspace/PipelineStrip";
import { useSession } from "@/components/workspace/session";
import {
  csrfHeaders,
  parseSseFrames,
  pumpStream,
  ws,
  type PipelineState,
} from "@/lib/api/workspace";
import { cn } from "@/lib/utils";

interface ChatBadge {
  kind: string;
  label: string;
}

interface PlanOffer {
  plan_entry_id: number;
  plan_slug: string;
  task_titles: string[];
  category: string;
}

interface ChatTask {
  id: number;
  title: string;
  // The ledger intake payload currently carries id + title only; state and
  // pipeline arrive when the turn references enriched rows. The card renders
  // the shared pill and the mini strip only where that data is present.
  state?: string;
  pipeline?: PipelineState | null;
}

interface ChatResult {
  message_id: string;
  reply: string;
  question: string | null;
  slice: { id: number; title: string } | null;
  tasks: ChatTask[];
  created: number;
  badge: ChatBadge | null;
  offer: PlanOffer | null;
}

interface Turn {
  id: number;
  role: "user" | "assistant";
  text: string;
  // Client-side wall time: bubbles show when each turn landed. The ledger
  // owns turn order; this is display-only and never sent anywhere.
  at: number;
  status: "done" | "streaming" | "error";
  error: string;
  retryNote: string;
  question: string | null;
  slice: { id: number; title: string } | null;
  tasks: ChatTask[];
  badge: ChatBadge | null;
  offer: PlanOffer | null;
}

const EMPTY_TURN = { question: null, slice: null, tasks: [], badge: null, offer: null };

// The ledger streams the model envelope (JSON with answer/intake/slice)
// as a token, then a result with the sentence the person should read.
// Showing the envelope prints `{"stub":true,...}` into the thread.
function spokenTurn(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed.startsWith("{")) return raw;
  try {
    const obj = JSON.parse(trimmed) as Record<string, unknown>;
    if (obj.stub === true) return "";
    const answer = typeof obj.answer === "string" ? obj.answer.trim() : "";
    const question = typeof obj.question === "string" ? obj.question.trim() : "";
    if (answer) return answer;
    if (question) return question;
    if ("intake" in obj || "slice" in obj || "tasks" in obj || "detail" in obj) return "";
  } catch {
    return "";
  }
  return raw;
}

function errorDetail(raw: string): string {
  try {
    const obj = JSON.parse(raw) as { detail?: unknown };
    if (typeof obj.detail === "string" && obj.detail.trim()) return obj.detail;
  } catch {
    /* already a sentence */
  }
  return raw;
}

// Intake badge tones mirror TaskStatePill (src/components/ui/StatusPill.tsx):
// a created task reads as open blue, a saved knowledge entry as done green,
// a plain log append as neutral steel. Theme tokens only, no hard-coded hex.
const INTAKE_BADGE_TONES: Record<string, { pill: string; dot: string }> = {
  task: { pill: "border-brand/40 text-brand-300 bg-brand/10", dot: "bg-brand" },
  saved: { pill: "border-success/35 text-success bg-success/10", dot: "bg-success" },
  logged: { pill: "border-steel/60 text-muted bg-navy/60", dot: "bg-steel-400" },
};

const INTAKE_BADGE_FALLBACK = { pill: "border-steel/60 text-muted bg-navy/60", dot: "bg-steel-400" };

// Empty-state examples: illustrative prompts only, each visibly labelled
// "Example". Clicking one prefills the composer; nothing is sent until the
// user presses Send.
const EXAMPLE_PROMPTS = [
  "Slice onboarding into small tasks",
  "What is blocked right now and why?",
  "Turn this thread into a plan I can convert",
  "Which tasks are waiting for review?",
  "Prepare a plan from this conversation",
];

// Rotating help text under the composer: one labelled example at a time,
// cycling every few seconds. Clicking prefills the composer; nothing is
// sent until the user presses Send.
function ExampleRotator({ onPick }: { onPick: (prompt: string) => void }) {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setIndex((i) => (i + 1) % EXAMPLE_PROMPTS.length), 4000);
    return () => clearInterval(t);
  }, []);
  const prompt = EXAMPLE_PROMPTS[index % EXAMPLE_PROMPTS.length];
  return (
    <div
      data-chat-examples
      className="mt-2 flex min-w-0 items-center gap-2 text-[13px] text-dim"
    >
      <span className="shrink-0 rounded-md border border-brand/40 bg-brand/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-brand-300">
        Example
      </span>
      <button
        type="button"
        data-example-prompt
        onClick={() => onPick(prompt)}
        title="Click to use this example"
        aria-label={`Use example prompt: ${prompt}`}
        className="truncate text-left transition-colors hover:text-fog"
      >
        {prompt}
      </button>
    </div>
  );
}

type PlanAction = "convert" | "edit" | "keep";

export default function ChatPage() {
  const router = useRouter();
  const {
    project: sessionProject,
    projects,
    setProject: setSessionProject,
    status: sessionStatus,
  } = useSession();
  // Single source of truth: the session's active project (header switcher
  // and this picker both write it, every surface reads it). Free text that
  // is not a registered project never becomes the target.
  const shownProject = sessionProject ?? "";
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the latest frame.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  function patchTurn(id: number, patch: Partial<Turn>) {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  function blankTurn(role: "user" | "assistant", text: string): Turn {
    return {
      id: nextId.current++,
      role,
      text,
      // eslint-disable-next-line react-hooks/purity -- wall-clock stamp for bubble display only, set at turn creation
      at: Date.now(),
      status: "done",
      error: "",
      retryNote: "",
      ...EMPTY_TURN,
    };
  }

  async function send(e?: React.FormEvent, retryText?: string) {
    e?.preventDefault();
    const outgoing = (retryText ?? message).trim();
    if (!outgoing || busy) return;
    if (retryText === undefined) setMessage("");
    await runTurn(outgoing, {});
  }

  async function act(action: PlanAction, entryId: number) {
    if (busy) return;
    // Edit carries the draft box as its revision note ("revise" when empty);
    // Convert/Just-keep-it send an explicit plan_action either way.
    const note = action === "edit" ? message.trim() || "revise" : action;
    if (action === "edit" && message.trim()) setMessage("");
    await runTurn(note, { plan_action: action, plan_entry: entryId });
  }

  async function runTurn(outgoing: string, extra: { plan_action?: string; plan_entry?: number }) {
    const effectiveProject = shownProject.trim() || sessionProject || "";
    if (!effectiveProject) {
      if (sessionStatus === "anonymous") {
        router.push("/workspace/login");
        return;
      }
      const userTurn = blankTurn("user", outgoing);
      const asstTurn: Turn = {
        ...blankTurn("assistant", ""),
        status: "error",
        error: "no project available for this tenant yet",
      };
      setTurns((prev) => [...prev, userTurn, asstTurn]);
      return;
    }
    setBusy(true);
    const userId = nextId.current++;
    const asstId = nextId.current++;
    setTurns((prev) => [
      ...prev,
      {
        id: userId,
        role: "user",
        text: outgoing,
        at: Date.now(),
        status: "done",
        error: "",
        retryNote: "",
        ...EMPTY_TURN,
      },
      {
        id: asstId,
        role: "assistant",
        text: "",
        at: Date.now(),
        status: "streaming",
        error: "",
        retryNote: "",
        ...EMPTY_TURN,
      },
    ]);
    try {
      const { response } = await ws.POST("/api/chat", {
        parseAs: "stream",
        body: { message: outgoing, project: effectiveProject, ...extra },
        headers: csrfHeaders(),
      });
      if (response.status === 401) {
        router.push("/workspace/login");
        patchTurn(asstId, { status: "error", error: "not signed in" });
        return;
      }
      if (!response.ok || !response.body) {
        const detail = await response.text().catch(() => "");
        patchTurn(asstId, {
          status: "error",
          error: errorDetail(detail) || `chat failed (${response.status})`,
          text: "",
        });
        setMessage(outgoing);
        return;
      }
      const noteFor = (frames: { event: string; data: string }[]): string => {
        const retries = frames.filter((f) => f.event === "retrying");
        if (retries.length === 0) return "";
        try {
          const last = JSON.parse(retries[retries.length - 1].data) as {
            attempt?: string | number;
          };
          return `retrying (attempt ${String(last.attempt ?? "?")}) — the model turn came back empty`;
        } catch {
          return "retrying — the model turn came back empty";
        }
      };
      let all = "";
      let seen = "";
      await pumpStream(response, (text) => {
        all += text;
        const frames = parseSseFrames(all);
        const tokens = frames
          .filter((f) => f.event === "token")
          .map((f) => {
            try {
              return spokenTurn((JSON.parse(f.data) as { text?: string }).text ?? "");
            } catch {
              return "";
            }
          })
          .join("");
        if (tokens !== seen) {
          seen = tokens;
          patchTurn(asstId, { text: tokens });
        }
        const note = noteFor(frames);
        if (note) patchTurn(asstId, { retryNote: note });
      });
      const frames = parseSseFrames(all);
      const errors = frames.filter((f) => f.event === "error");
      if (errors.length > 0) {
        patchTurn(asstId, { status: "error", error: errorDetail(errors[0].data), text: "" });
        setMessage(outgoing);
        return;
      }
      const finals = frames.filter((f) => f.event === "result");
      if (finals.length === 0) {
        patchTurn(asstId, { status: "error", error: "the model turn produced no result" });
        setMessage(outgoing);
        return;
      }
      const parsed = JSON.parse(finals[finals.length - 1].data) as ChatResult;
      patchTurn(asstId, {
        text: parsed.reply || seen,
        status: "done",
        question: parsed.question,
        slice: parsed.slice,
        tasks: parsed.tasks ?? [],
        badge: parsed.badge ?? null,
        offer: parsed.offer ?? null,
      });
    } catch (err) {
      patchTurn(asstId, {
        status: "error",
        error: err instanceof Error ? err.message : "chat failed",
      });
      setMessage(outgoing);
    } finally {
      setBusy(false);
    }
  }

  function retry(turnId: number) {
    const idx = turns.findIndex((t) => t.id === turnId);
    const userTurn = idx > 0 ? turns[idx - 1] : null;
    if (!userTurn || userTurn.role !== "user") return;
    // Drop the failed assistant turn; the retry re-appends a fresh one.
    setTurns((prev) => prev.filter((t) => t.id !== turnId));
    void send(undefined, userTurn.text);
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col">
      <div data-chat-header className="sticky top-0 z-10 bg-abyss pb-4">
        <h1 className="text-2xl font-semibold text-white">Supervisor chat</h1>
        <p className="mt-1 text-sm text-muted">
          Rouvr turns requests into tracked work: contract, build, review, evidence.
        </p>
        <label className="mt-6 block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Project
          </span>
          <Combobox
            id="chat-project-filter"
            value={shownProject}
            onChange={(v) => {
              if (projects.some((p) => p.project === v)) setSessionProject(v);
            }}
            options={projects.map((p) => p.project)}
            ariaLabel="Filter chat by project — type to filter"
            placeholder="project"
            inputClassName="h-10 w-full rounded-xl border border-steel/60 bg-abyss px-4 font-mono text-sm text-cloud outline-none placeholder:text-muted/60 focus:border-brand/60"
          />
        </label>
      </div>
      <div
        data-chat-thread
        className="mt-2 max-h-[55vh] space-y-4 overflow-y-auto pr-1"
      >
        {turns.map((turn) =>
          turn.role === "user" ? (
            <div key={turn.id} data-turn="user" className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-md bg-brand px-4 py-2">
                <p className="whitespace-pre-wrap text-sm text-abyss">
                  {turn.text}
                </p>
                <time
                  dateTime={new Date(turn.at).toISOString()}
                  className="mt-1 block text-right font-mono text-[10px] text-abyss/70"
                >
                  {new Date(turn.at).toLocaleTimeString()}
                </time>
              </div>
            </div>
          ) : (
            <div key={turn.id} data-turn="assistant" className="flex justify-start">
              <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-steel/40 bg-abyss/60 p-4">
                <time
                  dateTime={new Date(turn.at).toISOString()}
                  className="mb-1 block font-mono text-[10px] text-dim"
                >
                  {new Date(turn.at).toLocaleTimeString()}
                </time>
                {turn.retryNote ? (
                  <p className="mb-2 font-mono text-xs text-warning">{turn.retryNote}</p>
                ) : null}
                <p className="whitespace-pre-wrap text-sm text-cloud">
                  {turn.text}
                  {turn.status === "streaming" ? (
                    <span className="animate-blink">_</span>
                  ) : null}
                </p>
                {turn.status === "done" && turn.badge ? (
                  <p className="mt-3">
                    {/* Intake badge: one prominent pill per reply. Labels come
                        from the ledger intake router — "Logged" (appended to
                        the conversation log), "Saved: <slug>" (knowledge
                        entry written), "Task #N" (task created). Tones mirror
                        TaskStatePill via INTAKE_BADGE_TONES above. */}
                    {(() => {
                      const tone = INTAKE_BADGE_TONES[turn.badge.kind] ?? INTAKE_BADGE_FALLBACK;
                      return (
                        <span
                          data-intake-badge
                          data-badge-kind={turn.badge.kind}
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-xs",
                            tone.pill,
                          )}
                        >
                          <span className={cn("size-1.5 rounded-full", tone.dot)} aria-hidden />
                          {turn.badge.label}
                        </span>
                      );
                    })()}
                  </p>
                ) : null}
                {turn.status === "done" && turn.question ? (
                  <div className="mt-3 rounded-xl border border-warning/35 bg-warning/5 p-3">
                    <p className="mb-1 font-mono text-xs uppercase tracking-wider text-warning">
                      clarifying question
                    </p>
                    <p className="text-sm text-cloud">{turn.question}</p>
                  </div>
                ) : null}
                {turn.status === "done" && turn.offer ? (
                  <div
                    data-plan-offer
                    data-plan-entry={turn.offer.plan_entry_id}
                    className="mt-3 rounded-xl border border-brand/40 bg-brand/5 p-3"
                  >
                    <p className="mb-1 font-mono text-xs uppercase tracking-wider text-brand-300">
                      plan offer
                    </p>
                    <p data-plan-offer-title className="mb-2 text-sm font-medium text-cloud">
                      {turn.offer.plan_slug}{" "}
                      <span className="font-mono text-xs text-muted">
                        · {turn.offer.task_titles.length}{" "}
                        {turn.offer.task_titles.length === 1 ? "task" : "tasks"}
                      </span>
                    </p>
                    <ul className="mb-3 list-disc space-y-1 pl-5 text-sm text-cloud">
                      {turn.offer.task_titles.map((t) => (
                        <li key={t}>{t}</li>
                      ))}
                    </ul>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="primary"
                        data-plan-action="convert"
                        onClick={() => void act("convert", turn.offer!.plan_entry_id)}
                      >
                        Convert
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        data-plan-action="edit"
                        onClick={() => void act("edit", turn.offer!.plan_entry_id)}
                      >
                        Edit
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        data-plan-action="keep"
                        onClick={() => void act("keep", turn.offer!.plan_entry_id)}
                      >
                        Just keep it
                      </Button>
                    </div>
                  </div>
                ) : null}
                {turn.status === "done" && !turn.question ? (
                  <ul className="mt-3 space-y-2">
                    {turn.slice ? (
                      <li key={`slice-${turn.slice.id}`}>
                        <Link
                          href={`/workspace/tasks/${turn.slice.id}`}
                          className="block rounded-xl border border-brand/40 bg-brand/5 p-3 text-sm text-cloud transition-colors hover:border-brand/70"
                        >
                          <span className="font-mono text-xs text-brand-300">
                            slice #{turn.slice.id}
                          </span>{" "}
                          {turn.slice.title}
                        </Link>
                      </li>
                    ) : null}
                    {turn.tasks.map((t) => (
                      <li key={t.id}>
                        <Link
                          href={`/workspace/tasks/${t.id}`}
                          data-task-card
                          data-task-id={t.id}
                          className="block rounded-xl border border-steel/30 bg-navy/60 p-3 text-sm text-cloud transition-colors hover:border-brand/50"
                        >
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs text-muted">#{t.id}</span>
                            <span className="font-medium">{t.title}</span>
                            {t.state ? <TaskStatePill state={t.state} /> : null}
                          </span>
                          {t.pipeline ? (
                            <span className="mt-2 block">
                              <PipelineStrip pipeline={t.pipeline} variant="mini" />
                            </span>
                          ) : null}
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {turn.status === "error" ? (
                  <div className="mt-3">
                    <p className="text-sm text-danger">
                      {turn.error.includes("csrf") || turn.error.includes("(403)")
                        ? "Your sign-in changed in another tab. Sign in again, then retry."
                        : turn.error}
                    </p>
                    {turn.error.includes("csrf") || turn.error.includes("(403)") ? (
                      <Link
                        href="/workspace/login"
                        className="mt-2 inline-block rounded-lg border border-brand/40 bg-brand/10 px-3 py-1.5 text-sm text-brand-300 transition-colors hover:border-brand/70"
                      >
                        Sign in again
                      </Link>
                    ) : (
                      <Button
                        type="button"
                        variant="secondary"
                        className="mt-2"
                        onClick={() => retry(turn.id)}
                      >
                        Retry
                      </Button>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>
      <form
        onSubmit={(e) => void send(e)}
        data-chat-composer
        className="sticky bottom-0 z-10 space-y-3 bg-abyss pb-2 pt-3"
      >
        <label className="block">
          <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-muted">
            Message
          </span>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(undefined);
              }
            }}
            rows={4}
            placeholder="e.g. Slice onboarding into small tasks"
            className="w-full rounded-xl border border-steel/60 bg-abyss p-4 text-sm text-cloud outline-none placeholder:text-muted/60 focus:border-brand/60"
          />
        </label>
        <Button type="submit" variant="primary" disabled={busy || !message.trim()}>
          {busy ? "Working…" : "Send"}
        </Button>
        <ExampleRotator onPick={(prompt) => setMessage(prompt)} />
      </form>
    </div>
  );
}
