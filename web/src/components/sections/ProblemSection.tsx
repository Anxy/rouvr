"use client";

import { m } from "motion/react";
import { Reveal, staggerChild, staggerParent } from "@/components/effects/Reveal";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Section } from "@/components/ui/Section";
import { problem } from "@/content/home";

export function ProblemSection() {
  return (
    <Section id="possibility">
      <div className="grid grid-cols-1 gap-14 lg:grid-cols-2 lg:gap-20">
        <div>
          <Reveal>
            <Eyebrow>{problem.eyebrow}</Eyebrow>
          </Reveal>
          <Reveal delay={0.05}>
            <h2 className="mt-5 text-balance text-3xl font-semibold tracking-tight text-white sm:text-5xl sm:leading-[1.08]">
              {problem.title}{" "}
              <span className="text-gradient md:animate-shimmer">{problem.highlight}</span>
            </h2>
          </Reveal>
          {problem.paragraphs.map((paragraph, i) => (
            <Reveal key={paragraph} delay={0.1 + i * 0.05}>
              <p className="mt-5 text-pretty leading-relaxed text-muted sm:text-lg">{paragraph}</p>
            </Reveal>
          ))}
        </div>

        <m.ul
          variants={staggerParent}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "0px 0px -15% 0px" }}
          className="relative flex flex-col justify-center gap-3"
        >
          <span
            aria-hidden
            className="pointer-events-none absolute bottom-8 left-[2.75rem] top-8 w-px -translate-x-1/2 bg-gradient-to-b from-transparent via-brand to-transparent shadow-[0_0_12px_rgb(8_169_240/0.85)]"
          />
          {problem.bullets.map((bullet, i) => (
              <m.li
                key={bullet}
                variants={staggerChild}
                className="glass group relative flex items-center gap-4 rounded-2xl px-5 py-4 transition-colors duration-300 hover:border-brand/50"
              >
                <span className="relative grid size-12 shrink-0 place-items-center">
                  <span
                    aria-hidden
                    className="absolute inset-1 rounded-full bg-brand/30 opacity-70 blur-md transition-opacity duration-300 group-hover:opacity-100"
                  />
                  <span className="relative grid size-12 place-items-center rounded-full border border-brand/50 bg-abyss text-brand-300 shadow-[0_0_24px_-8px_rgb(8_169_240/0.9)] transition duration-300 group-hover:border-brand group-hover:bg-brand group-hover:text-white group-hover:shadow-[0_0_28px_-4px_rgb(8_169_240/0.95)]">
                    <span className="font-mono text-[13px] tracking-[0.08em]">0{i + 1}</span>
                  </span>
                  <span className="absolute -right-1 -bottom-1 grid size-5 place-items-center rounded-full border border-brand/40 bg-abyss">
                    <svg viewBox="0 0 24 24" className="size-3" aria-hidden>
                      <m.path
                        d="M5 12.5 L10 17 L19 7.5"
                        fill="none"
                        stroke="currentColor"
                        className="text-brand"
                        strokeWidth="3"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        initial={{ pathLength: 0 }}
                        whileInView={{ pathLength: 1 }}
                        viewport={{ once: true }}
                        transition={{ duration: 0.55, delay: 0.25 + i * 0.12, ease: "easeOut" }}
                      />
                    </svg>
                  </span>
                </span>
                <span className="text-[15px] text-cloud sm:text-base">{bullet}</span>
              </m.li>
          ))}
        </m.ul>
      </div>
    </Section>
  );
}
