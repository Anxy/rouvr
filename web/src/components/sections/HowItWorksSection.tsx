"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Mascot } from "@/components/brand/Mascot";
import { BeamLine } from "@/components/effects/BeamLine";
import { Reveal } from "@/components/effects/Reveal";
import { Badge } from "@/components/ui/Badge";
import { Section, SectionHeading } from "@/components/ui/Section";
import { TerminalWindow } from "@/components/ui/TerminalWindow";
import { howItWorks } from "@/content/home";
import { cn } from "@/lib/utils";
import { StepScreen } from "./how-it-works/StepScreen";

export function HowItWorksSection() {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.index));
        }
      },
      { rootMargin: "-45% 0px -45% 0px" },
    );
    refs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);

  const step = howItWorks.steps[active];

  return (
    <Section id="how-it-works" className="scroll-mt-24">
      <SectionHeading eyebrow={howItWorks.eyebrow} title={howItWorks.title} />

      <div className="mt-16 grid grid-cols-1 gap-12 lg:mt-24 lg:grid-cols-2 lg:gap-16">
        <div className="relative">
          <BeamLine
            orientation="vertical"
            className="absolute bottom-0 left-[15px] top-0 h-auto sm:left-[19px]"
          />
          <ol className="relative">
            {howItWorks.steps.map((s, i) => (
              <li
                key={s.id}
                ref={(el) => {
                  refs.current[i] = el;
                }}
                data-index={i}
                className="flex gap-4 py-8 sm:gap-6 lg:min-h-[42vh] lg:py-14"
              >
                <span
                  className={cn(
                    "relative z-10 grid size-8 shrink-0 place-items-center rounded-full border bg-abyss font-mono text-[11px] transition-colors duration-500 sm:size-10 sm:text-xs",
                    i <= active
                      ? "border-brand text-brand-300 shadow-[0_0_24px_-4px_rgb(8_169_240/0.8)]"
                      : "border-steel text-muted",
                  )}
                >
                  {s.id}
                </span>
                <div
                  className={cn(
                    "min-w-0 flex-1 transition-opacity duration-500",
                    i === active ? "opacity-100" : "lg:opacity-45",
                  )}
                >
                  <h3 className="pt-0.5 text-lg font-semibold text-white sm:pt-0 sm:text-2xl">{s.title}</h3>
                  <p className="mt-3 max-w-md leading-relaxed text-muted">{s.body}</p>
                  <div className="mt-6 lg:hidden">
                    <div className="rounded-2xl border border-steel/50 bg-navy/70 p-3 sm:p-4">
                      <StepScreen index={i} />
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="hidden lg:block">
          <div className="sticky top-28">
            <TerminalWindow
              title={`rouvr / run / step-${step.id}`}
              meta={<Badge>Illustrative</Badge>}
              bodyClassName="min-h-[340px] p-6"
            >
              <div className="mb-6 flex items-center gap-4">
                <Mascot state={step.mascot} className="size-12" />
                <div>
                  <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-brand-300">
                    Step {step.id} / 0{howItWorks.steps.length}
                  </p>
                  <p className="text-lg font-semibold text-white">{step.title}</p>
                </div>
              </div>
              <AnimatePresence mode="wait">
                <m.div
                  key={active}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -12 }}
                  transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                >
                  <StepScreen index={active} />
                </m.div>
              </AnimatePresence>
            </TerminalWindow>
          </div>
        </div>
      </div>
      <Reveal className="mx-auto mt-16 max-w-3xl border-l-2 border-brand/70 pl-6">
        <p className="text-pretty text-lg leading-relaxed text-white sm:text-xl">&ldquo;{howItWorks.quote}&rdquo;</p>
        <p className="mt-5 leading-relaxed text-muted">{howItWorks.followUp}</p>
      </Reveal>
    </Section>
  );
}
