import { ArrowUpRight } from "lucide-react";
import { Mascot } from "@/components/brand/Mascot";
import { GlowBorder } from "@/components/effects/GlowBorder";
import { GridBackground } from "@/components/effects/GridBackground";
import { Reveal } from "@/components/effects/Reveal";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Section";
import { finalCta } from "@/content/home";

export function FinalCTA() {
  return (
    <Section className="pb-20">
      <Reveal y={30}>
        <GlowBorder innerClassName="relative overflow-hidden px-6 py-16 text-center sm:px-12 sm:py-24">
          <GridBackground className="opacity-50" />
          <div
            aria-hidden
            className="absolute left-1/2 top-0 h-72 w-[80%] -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              background: "radial-gradient(closest-side, rgb(8 169 240 / 0.35), transparent)",
            }}
          />
          <div className="relative">
            <Mascot
              state="done"
              className="mx-auto size-20 drop-shadow-[0_10px_30px_rgb(8_169_240/0.5)]"
            />
            <h2 className="mx-auto mt-8 max-w-3xl text-balance text-3xl font-semibold tracking-tight text-white sm:text-5xl sm:leading-[1.08]">
              {finalCta.title}
            </h2>
            <p className="mx-auto mt-5 max-w-xl text-pretty leading-relaxed text-muted sm:text-lg">
              {finalCta.body}
            </p>
            <p className="mx-auto mt-4 max-w-xl text-pretty leading-relaxed text-muted sm:text-lg">
              {finalCta.detail}
            </p>
            <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Button href={finalCta.primary.href} size="lg" className="w-full sm:w-auto">
                {finalCta.primary.label}
                <ArrowUpRight className="size-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </Button>
            </div>
            <p className="mt-10 text-sm font-medium text-brand-300">{finalCta.campaign}</p>
            <p className="mt-6 text-lg font-semibold text-white">{finalCta.tagline}</p>
            <p className="mt-2 text-muted">{finalCta.strap}</p>
          </div>
        </GlowBorder>
      </Reveal>
    </Section>
  );
}
