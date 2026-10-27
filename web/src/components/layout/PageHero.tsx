import { ArrowUpRight } from "lucide-react";
import { AuroraBlobs } from "@/components/effects/AuroraBlobs";
import { GridBackground } from "@/components/effects/GridBackground";
import { Reveal } from "@/components/effects/Reveal";
import { Button } from "@/components/ui/Button";
import { Container } from "@/components/ui/Container";
import { Eyebrow } from "@/components/ui/Eyebrow";

type Cta = { label: string; href: string };

export function PageHero({
  eyebrow,
  title,
  description,
  primaryCta,
  secondaryCta,
}: {
  eyebrow: string;
  title: string;
  description: string;
  primaryCta?: Cta;
  secondaryCta?: Cta;
}) {
  return (
    <section className="relative isolate overflow-hidden pb-16 pt-32 sm:pt-40">
      <div aria-hidden className="absolute inset-0 -z-10 bg-navy" />
      <AuroraBlobs className="-z-10" />
      <GridBackground className="-z-10 opacity-50" />
      <div
        aria-hidden
        className="absolute inset-x-0 bottom-0 -z-10 h-32 bg-gradient-to-b from-transparent to-abyss"
      />

      <Container>
        <Reveal>
          <Eyebrow>{eyebrow}</Eyebrow>
        </Reveal>
        <Reveal delay={0.05}>
          <h1 className="mt-6 max-w-4xl text-balance text-4xl font-semibold leading-[1.05] tracking-tight text-white sm:text-6xl">
            {title}
          </h1>
        </Reveal>
        <Reveal delay={0.1}>
          <p className="mt-6 max-w-2xl text-pretty leading-relaxed text-muted sm:text-lg">{description}</p>
        </Reveal>
        {(primaryCta || secondaryCta) && (
          <Reveal delay={0.15} className="mt-9 flex flex-col gap-3 sm:flex-row">
            {primaryCta && (
              <Button href={primaryCta.href} size="lg" className="w-full sm:w-auto">
                {primaryCta.label}
                <ArrowUpRight className="size-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </Button>
            )}
            {secondaryCta && (
              <Button href={secondaryCta.href} variant="secondary" size="lg" className="w-full sm:w-auto">
                {secondaryCta.label}
              </Button>
            )}
          </Reveal>
        )}
      </Container>
    </section>
  );
}
