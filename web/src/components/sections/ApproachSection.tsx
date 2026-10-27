import { BeamLine } from "@/components/effects/BeamLine";
import { GridBackground } from "@/components/effects/GridBackground";
import { Reveal } from "@/components/effects/Reveal";
import { Section, SectionHeading } from "@/components/ui/Section";
import { approach } from "@/content/home";

export function ApproachSection() {
  return (
    <Section id="approach" className="scroll-mt-20 overflow-hidden">
      <GridBackground variant="dot" className="opacity-50" />
      <SectionHeading eyebrow={approach.eyebrow} title={approach.title} />

      <div className="relative mt-20">
        <BeamLine className="absolute left-[10%] right-[10%] top-7 hidden w-auto lg:block" />
        <BeamLine
          orientation="vertical"
          className="absolute bottom-10 left-7 top-7 h-auto lg:hidden"
        />

        <ol className="relative grid grid-cols-1 gap-10 lg:grid-cols-5 lg:gap-6">
          {approach.stages.map((stage, i) => {
            const Icon = stage.icon;
            return (
              <li key={stage.title}>
                <Reveal
                  delay={i * 0.1}
                  className="flex gap-5 lg:flex-col lg:items-center lg:text-center"
                >
                  <div className="relative grid size-14 shrink-0 place-items-center rounded-2xl border border-brand/40 bg-navy shadow-[0_0_30px_-6px_rgb(8_169_240/0.6)]">
                    <Icon className="size-6 text-brand-300" aria-hidden />
                    <span className="absolute -right-2 -top-2 rounded-md border border-steel bg-abyss px-1.5 font-mono text-[10px] text-muted">
                      0{i + 1}
                    </span>
                  </div>
                  <div className="lg:mt-6">
                    <h3 className="text-lg font-semibold text-white">{stage.title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-muted">{stage.body}</p>
                  </div>
                </Reveal>
              </li>
            );
          })}
        </ol>
      </div>
      <Reveal className="mx-auto mt-12 max-w-3xl space-y-3 text-center">
        {approach.caption.map((line) => (
          <p key={line} className="text-pretty text-sm leading-relaxed text-muted sm:text-base">
            {line}
          </p>
        ))}
      </Reveal>
    </Section>
  );
}
