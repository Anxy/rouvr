import { ArrowDown, ArrowUpRight } from "lucide-react";
import { AuroraBlobs } from "@/components/effects/AuroraBlobs";
import { GridBackground } from "@/components/effects/GridBackground";
import { Marquee } from "@/components/effects/Marquee";
import { Reveal } from "@/components/effects/Reveal";
import { Button } from "@/components/ui/Button";
import { Container } from "@/components/ui/Container";
import { hero, marqueeItems } from "@/content/home";
import { HeroHeadline } from "./hero/HeroHeadline";
import { WorkflowDemo } from "./hero/WorkflowDemo";

export function Hero() {
  return (
    <section id="meet" className="relative isolate scroll-mt-24 overflow-hidden pb-8 pt-32 sm:pt-40">
      <div aria-hidden className="absolute inset-0 -z-10 bg-navy" />
      <AuroraBlobs className="-z-10" />
      <GridBackground className="-z-10 opacity-60" />
      <div
        aria-hidden
        className="absolute inset-x-0 bottom-0 -z-10 h-40 bg-gradient-to-b from-transparent to-abyss"
      />

      <Container className="grid grid-cols-1 items-center gap-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-12">
        <div>
          <HeroHeadline
            text={hero.title}
            highlightFrom={1}
            className="text-[2.6rem] font-semibold leading-[1.04] tracking-tight text-white sm:text-6xl lg:text-[4.25rem]"
          />
          <Reveal delay={0.45}>
            <p className="mt-5 max-w-xl text-pretty text-xl font-medium leading-snug text-white sm:text-2xl">
              {hero.strap}
            </p>
          </Reveal>
          <Reveal delay={0.5}>
            <p className="mt-6 max-w-xl text-pretty text-base leading-relaxed text-muted sm:text-lg">
              {hero.description}
            </p>
          </Reveal>
          {hero.paragraphs.map((paragraph, i) => (
            <Reveal key={paragraph} delay={0.55 + i * 0.05}>
              <p className="mt-4 max-w-xl text-pretty leading-relaxed text-muted">{paragraph}</p>
            </Reveal>
          ))}
          <Reveal delay={0.6} className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Button href={hero.primaryCta.href} size="lg" className="w-full sm:w-auto">
              {hero.primaryCta.label}
              <ArrowUpRight className="size-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
            </Button>
            <Button
              href={hero.secondaryCta.href}
              variant="secondary"
              size="lg"
              className="w-full sm:w-auto"
            >
              {hero.secondaryCta.label}
              <ArrowDown className="size-4 transition-transform duration-300 group-hover:translate-y-0.5" />
            </Button>
          </Reveal>
        </div>

        <Reveal delay={0.3} y={28}>
          <WorkflowDemo />
        </Reveal>
      </Container>

      <Container className="mt-14">
        <Marquee items={marqueeItems} />
      </Container>
    </section>
  );
}
