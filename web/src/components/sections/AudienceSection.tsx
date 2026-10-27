import type { LucideIcon } from "lucide-react";
import { FileCheck, Radio, Send } from "lucide-react";
import { Reveal } from "@/components/effects/Reveal";
import { SpotlightCard } from "@/components/effects/SpotlightCard";
import { Section, SectionHeading } from "@/components/ui/Section";
import { StatusPill, type Status } from "@/components/ui/StatusPill";
import { audience } from "@/content/home";
import { cn } from "@/lib/utils";

const recoveryTone: Record<string, Status> = {
  Verified: "passed",
  "Failed or uncertain": "failed",
  "New authority needed": "review",
  "Check again after repair": "running",
};

const returnMarks: Record<string, { status: Status; icon: LucideIcon }> = {
  "Monitoring active": { status: "running", icon: Radio },
  "Your decision": { status: "review", icon: Send },
  Delivered: { status: "passed", icon: FileCheck },
};

type Row = {
  title: string;
  icon?: LucideIcon;
  body?: string;
  detail?: string;
  status?: string;
  next?: string;
};

type AudienceContent = {
  eyebrow: string;
  title: string;
  description?: string;
  visualTitle?: string;
  visualLabel?: string;
  caption?: string;
  note?: string;
  recovery?: { title: string; detail: string; note?: string }[];
  recoveryNotes?: string[];
  items: Row[];
};

export function AudienceSection({
  id = "continuity",
  content = audience,
}: {
  id?: string;
  content?: AudienceContent;
}) {
  const rows = content.items.some((item) => item.status);

  return (
    <Section id={id} className="scroll-mt-20">
      <SectionHeading eyebrow={content.eyebrow} title={content.title} description={content.description} />

      {rows ? (
        <Reveal className="relative mx-auto mt-12 max-w-4xl overflow-hidden rounded-3xl border border-white/10 bg-navy/50 p-5 shadow-[0_30px_80px_-48px_rgb(8_169_240/0.55)] sm:p-8">
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-[radial-gradient(ellipse_at_top,rgb(8_169_240/0.16),transparent_70%)]"
          />
          <div className="relative flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            {content.visualTitle && <p className="text-lg font-semibold text-white sm:text-xl">{content.visualTitle}</p>}
            {content.visualLabel && (
              <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-brand-300">{content.visualLabel}</p>
            )}
          </div>
          <ul className="relative mt-6 space-y-3">
            {content.items.map((item) => {
              const mark = returnMarks[item.status ?? ""] ?? { status: "running" as Status, icon: Radio };
              const Icon = mark.icon;
              const waiting = mark.status === "review";
              return (
                <li
                  key={item.title}
                  className={cn(
                    "grid grid-cols-1 gap-3 rounded-2xl border bg-abyss/60 px-4 py-4 sm:grid-cols-[auto_minmax(0,1.3fr)_minmax(0,1fr)] sm:items-center sm:gap-5 sm:px-5",
                    waiting ? "border-warning/45 shadow-[0_0_28px_-16px_rgb(251_191_36/0.8)]" : "border-white/10",
                  )}
                >
                  <span
                    className={cn(
                      "grid size-10 place-items-center rounded-xl border",
                      waiting
                        ? "border-warning/40 bg-warning/10 text-warning"
                        : mark.status === "passed"
                          ? "border-success/35 bg-success/10 text-success"
                          : "border-brand/40 bg-brand/10 text-brand-300",
                    )}
                  >
                    <Icon className="size-4" aria-hidden />
                  </span>
                  <div>
                    <p className="font-semibold text-white">{item.title}</p>
                    <p className="mt-1 text-sm leading-relaxed text-muted">{item.detail}</p>
                  </div>
                  <div className="sm:text-right">
                    <StatusPill status={mark.status} label={item.status} />
                    <p className="mt-2 text-sm leading-relaxed text-muted">{item.next}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        </Reveal>
      ) : (
        <ul className="mt-16 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {content.items.map((item, i) => {
            const Icon = item.icon;
            return (
              <li key={item.title}>
                <Reveal delay={i * 0.08} className="h-full">
                  <SpotlightCard className="group h-full p-6">
                    {Icon && (
                      <span className="grid size-11 place-items-center rounded-xl border border-steel bg-abyss transition-colors duration-300 group-hover:border-brand/60">
                        <Icon className="size-5 text-brand-300" aria-hidden />
                      </span>
                    )}
                    <h3 className="mt-6 text-lg font-semibold text-white">{item.title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-muted">{item.body}</p>
                  </SpotlightCard>
                </Reveal>
              </li>
            );
          })}
        </ul>
      )}

      {content.recovery && (
        <Reveal className="mt-12">
          <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {content.recovery.map((step, i) => (
              <li
                key={step.title}
                className="flex h-full flex-col rounded-2xl border border-white/10 bg-navy/50 p-5 transition duration-300 hover:-translate-y-0.5 hover:border-brand/45"
              >
                <span className="grid size-9 place-items-center rounded-full border border-brand/40 bg-abyss font-mono text-[11px] tracking-[0.08em] text-brand-300 shadow-[0_0_18px_-8px_rgb(8_169_240/0.9)]">
                  0{i + 1}
                </span>
                <p className="mt-4 font-semibold text-white">{step.title}</p>
                <p className="mt-1 text-sm leading-relaxed text-muted">{step.detail}</p>
                {step.note && (
                  <div className="mt-auto pt-4">
                    <StatusPill status={recoveryTone[step.note] ?? "running"} label={step.note} />
                  </div>
                )}
              </li>
            ))}
          </ol>
          {(content.recoveryNotes || content.caption) && (
            <div className="mt-4 rounded-2xl border border-white/10 bg-abyss/40 px-5 py-5 sm:px-6">
              {content.recoveryNotes && (
                <ul className="space-y-3">
                  {content.recoveryNotes.map((note) => (
                    <li key={note} className="flex gap-3 text-sm leading-relaxed text-muted sm:text-base">
                      <span className="mt-2 size-1.5 shrink-0 rounded-full bg-brand" aria-hidden />
                      <span>{note}</span>
                    </li>
                  ))}
                </ul>
              )}
              {content.caption && (
                <p
                  className={cn(
                    "text-sm leading-relaxed text-cloud sm:text-base",
                    content.recoveryNotes && "mt-4 border-t border-white/10 pt-4",
                  )}
                >
                  {content.caption}
                </p>
              )}
            </div>
          )}
        </Reveal>
      )}

      {content.caption && !content.recovery && (
        <Reveal>
          <p className="mx-auto mt-10 max-w-3xl text-center text-pretty text-sm leading-relaxed text-muted sm:text-base">
            {content.caption}
          </p>
        </Reveal>
      )}
      {content.note && (
        <Reveal>
          <p className="mx-auto mt-4 max-w-3xl text-center text-pretty text-lg font-medium text-white">
            {content.note}
          </p>
        </Reveal>
      )}
    </Section>
  );
}
