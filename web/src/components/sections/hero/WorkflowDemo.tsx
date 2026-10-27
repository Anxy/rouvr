"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, m, useInView, useReducedMotion } from "motion/react";
import { Check, Loader2 } from "lucide-react";
import { Mascot, type MascotState } from "@/components/brand/Mascot";
import { TypingText } from "@/components/effects/TypingText";
import { Badge } from "@/components/ui/Badge";
import { StatusPill } from "@/components/ui/StatusPill";
import { TerminalWindow } from "@/components/ui/TerminalWindow";
import { hero } from "@/content/home";
import { cn } from "@/lib/utils";

const STEP_MS = 1700;
const HOLD_MS = 3200;

/**
 * Illustrative, scripted walkthrough. Not real product output:
 * cycles typing -> steps -> awaiting review, then restarts.
 */
export function WorkflowDemo() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { margin: "-10% 0px" });
  const reduce = useReducedMotion();
  const steps = hero.steps;

  const [cycle, setCycle] = useState(0);
  const [typed, setTyped] = useState(false);
  const [active, setActive] = useState(-1);
  const shownActive = reduce ? steps.length : active;

  useEffect(() => {
    if (!typed || reduce || !inView) return;
    const delay = active >= steps.length ? HOLD_MS : active < 0 ? 500 : STEP_MS;
    const t = setTimeout(() => {
      if (active >= steps.length) {
        setTyped(false);
        setActive(-1);
        setCycle((c) => c + 1);
      } else {
        setActive((a) => a + 1);
      }
    }, delay);
    return () => clearTimeout(t);
  }, [typed, active, inView, reduce, steps.length]);

  const complete = shownActive >= steps.length;
  const mascotState: MascotState = complete
    ? "done"
    : shownActive >= 0
      ? steps[shownActive].mascot
      : "idle";
  const progress = Math.max(0, Math.min(shownActive, steps.length)) / steps.length;

  return (
    <div ref={ref} className="relative">
      <div
        aria-hidden
        className="absolute -inset-10 -z-10 rounded-[3rem] opacity-70"
        style={{
          background:
            "radial-gradient(60% 60% at 50% 40%, rgb(8 169 240 / 0.25), transparent 70%)",
        }}
      />
      <TerminalWindow
        title="rouvr / objective"
        meta={
          <Badge>
            Illustrative<span className="hidden sm:inline"> workflow</span>
          </Badge>
        }
        bodyClassName="p-0"
      >
        <div className="relative border-b border-steel/40 p-5">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-brand-300">
            Your objective
          </p>
          <p className="mt-2 min-h-[3.25rem] pr-16 text-[15px] leading-relaxed text-white">
            <span className="text-muted">&ldquo;</span>
            <TypingText
              key={cycle}
              text={hero.objective}
              speed={24}
              startDelay={cycle === 0 ? 900 : 300}
              onDone={() => setTyped(true)}
            />
            <span className="text-muted">&rdquo;</span>
          </p>
          <Mascot
            state={mascotState}
            className="absolute right-4 top-4 size-14 drop-shadow-[0_6px_18px_rgb(8_169_240/0.45)]"
          />
        </div>

        <ol className="relative space-y-1 p-3">
          {steps.map((step, i) => {
            const state = complete || i < shownActive ? "done" : i === shownActive ? "running" : "queued";
            return (
              <li
                key={step.id}
                className={cn(
                  "relative flex items-start gap-4 rounded-xl px-3 py-3 transition-colors duration-500",
                  state === "running" && "bg-brand/[0.07]",
                )}
              >
                <div
                  className={cn(
                    "grid size-8 shrink-0 place-items-center rounded-lg border font-mono text-xs transition-colors duration-500",
                    state === "done" && "border-success/40 bg-success/10 text-success",
                    state === "running" && "border-brand/60 bg-brand/15 text-brand-300",
                    state === "queued" && "border-steel/70 text-muted",
                  )}
                >
                  <AnimatePresence mode="wait" initial={false}>
                    {state === "done" ? (
                      <m.span
                        key="done"
                        initial={{ scale: 0.4, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.4, opacity: 0 }}
                      >
                        <Check className="size-4" />
                      </m.span>
                    ) : state === "running" ? (
                      <m.span key="run" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                        <Loader2 className="size-4 animate-spin" />
                      </m.span>
                    ) : (
                      <m.span key="q" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                        {step.id}
                      </m.span>
                    )}
                  </AnimatePresence>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-3">
                    <p
                      className={cn(
                        "text-sm font-medium transition-colors duration-500",
                        state === "queued" ? "text-cloud/60" : "text-white",
                      )}
                    >
                      {step.title}
                    </p>
                    <StatusPill
                      status={state === "done" ? "passed" : state}
                      label={state === "done" ? "Complete" : undefined}
                      className="hidden sm:inline-flex"
                    />
                  </div>
                  <p className="mt-0.5 text-xs text-muted">{step.detail}</p>
                </div>
              </li>
            );
          })}
        </ol>

        <div className="border-t border-steel/40 px-5 py-4">
          <div className="flex items-center justify-between font-mono text-[11px] text-muted">
            <span>
              {complete
                ? hero.doneLabel
                : shownActive >= 0
                  ? `Step ${steps[shownActive].id} of 0${steps.length}`
                  : hero.waitingLabel}
            </span>
            <span className={complete ? "text-success" : "text-brand-300"}>
              {Math.round(progress * 100)}%
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-steel/40">
            <m.div
              className="h-full origin-left rounded-full bg-gradient-to-r from-brand to-brand-300"
              initial={false}
              animate={{ scaleX: progress }}
              transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
            />
          </div>
        </div>
      </TerminalWindow>
    </div>
  );
}
