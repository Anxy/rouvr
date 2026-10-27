"use client";

import { Reveal } from "@/components/effects/Reveal";
import { Badge } from "@/components/ui/Badge";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Section } from "@/components/ui/Section";
import { TerminalWindow } from "@/components/ui/TerminalWindow";
import { evidence } from "@/content/home";

export function EvidenceSection() {
  return (
    <Section id="evidence" className="scroll-mt-20">
      <div className="relative grid grid-cols-1 items-center gap-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-16">
        <div>
          <Reveal>
            <Eyebrow>{evidence.eyebrow}</Eyebrow>
          </Reveal>
          <Reveal delay={0.05}>
            <h2 className="mt-5 text-balance text-3xl font-semibold tracking-tight text-white sm:text-5xl sm:leading-[1.08]">
              {evidence.title}
            </h2>
          </Reveal>
          {evidence.paragraphs.map((paragraph, i) => (
            <Reveal key={paragraph} delay={0.1 + i * 0.05}>
              <p className="mt-5 text-pretty leading-relaxed text-muted sm:text-lg">{paragraph}</p>
            </Reveal>
          ))}
          <Reveal delay={0.2}>
            <p className="mt-8 font-medium leading-relaxed text-white">{evidence.tagline}</p>
          </Reveal>
        </div>

        <Reveal delay={0.1} y={28}>
          <TerminalWindow
            title="engineering / delivery"
            meta={<Badge>Client anonymised</Badge>}
            bodyClassName="p-0"
          >
            <div className="border-b border-steel/40 px-5 py-5">
              <p className="font-mono text-[10px] uppercase tracking-widest text-muted">{evidence.caseLabel}</p>
              <h3 className="mt-2 text-lg font-semibold text-white sm:text-xl">{evidence.caseTitle}</h3>
            </div>
            <div className="grid grid-cols-1 divide-y divide-steel/40 border-b border-steel/40 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
              {evidence.stats.map((stat) => (
                <div key={stat.label} className="px-5 py-4">
                  <p className="text-2xl font-semibold tracking-tight text-white">{stat.value}</p>
                  <p className="mt-1 text-sm leading-relaxed text-muted">{stat.label}</p>
                </div>
              ))}
            </div>
            <p className="px-5 py-5 text-sm leading-relaxed text-muted">{evidence.caseBody}</p>
          </TerminalWindow>
        </Reveal>
      </div>
    </Section>
  );
}
