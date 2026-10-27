import { Reveal } from "@/components/effects/Reveal";
import { SpotlightCard } from "@/components/effects/SpotlightCard";
import { Section, SectionHeading } from "@/components/ui/Section";
import { SectionLink } from "@/components/ui/SectionLink";
import { useCases } from "@/content/home";

const featured = useCases.items[0];
const worlds = useCases.items.slice(1);

export function UseCasesSection() {
  const FeaturedIcon = featured.icon;

  return (
    <Section id="pack" className="scroll-mt-20">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <SectionHeading
          align="left"
          eyebrow={useCases.eyebrow}
          title={useCases.title}
          className="max-w-xl"
        />
        <Reveal className="max-w-md lg:pb-1">
          <p className="text-pretty leading-relaxed text-muted sm:text-lg">{useCases.label}</p>
        </Reveal>
      </div>

      <article className="mt-12 overflow-hidden rounded-3xl border border-white/10 bg-navy/50 shadow-[0_30px_80px_-48px_rgb(8_169_240/0.7)]">
        <div className="grid lg:grid-cols-[minmax(0,0.86fr)_minmax(0,1.14fr)]">
          <div className="relative border-b border-white/10 p-6 sm:p-8 lg:border-b-0 lg:border-r">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(ellipse_at_top_left,rgb(8_169_240/0.22),transparent_68%)]"
            />
            <div className="relative">
              <div className="flex items-center justify-between gap-3">
                <span className="grid size-12 place-items-center rounded-2xl border border-brand/40 bg-abyss shadow-[0_0_28px_-8px_rgb(8_169_240/0.85)]">
                  <FeaturedIcon className="size-5 text-brand-300" aria-hidden />
                </span>
                <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.14em] text-brand-300">
                  {featured.tab}
                </span>
              </div>
              <p className="mt-8 font-mono text-[11px] uppercase tracking-[0.16em] text-brand-300">
                {featured.kicker}
              </p>
              <h3 className="mt-3 text-2xl font-semibold tracking-tight text-white sm:text-3xl">
                {featured.title}
              </h3>
              {featured.quote && (
                <p className="mt-6 text-pretty text-lg leading-snug text-white sm:text-xl">
                  &ldquo;{featured.quote}&rdquo;
                </p>
              )}
            </div>
          </div>

          <ol className="space-y-0 p-6 sm:p-8">
            {featured.paragraphs.map((paragraph, i) => (
              <li key={paragraph} className="flex gap-4 border-b border-white/10 py-5 first:pt-0 last:border-b-0 last:pb-0">
                <span className="mt-1 font-mono text-[11px] tracking-[0.14em] text-brand-300">
                  0{i + 1}
                </span>
                <p className="leading-relaxed text-muted">{paragraph}</p>
              </li>
            ))}
          </ol>
        </div>

        {featured.outcomes && (
          <div className="border-t border-white/10 px-6 py-6 sm:px-8">
            <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
              {useCases.outcomeLabel}
            </p>
            <ul className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
              {featured.outcomes.map((outcome) => (
                <li
                  key={outcome.title}
                  className="rounded-2xl border border-brand/25 bg-brand/[0.06] px-4 py-4"
                >
                  <p className="font-semibold text-white">{outcome.title}</p>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted">{outcome.body}</p>
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="border-t border-brand/25 bg-brand/[0.07] px-6 py-4 font-medium text-white sm:px-8">
          {featured.result}
        </p>
      </article>

      <SectionLink href={useCases.link.href}>{useCases.link.label}</SectionLink>

      <div className="mt-8">
        <Reveal>
          <h3 className="max-w-3xl text-balance text-3xl font-semibold tracking-tight text-white sm:text-5xl sm:leading-[1.08]">
            {useCases.bridge.title}
          </h3>
        </Reveal>
        <Reveal delay={0.05}>
          <p className="mt-4 max-w-2xl text-pretty leading-relaxed text-muted sm:text-lg">
            {useCases.bridge.body}
          </p>
        </Reveal>

        <ul className="mt-10 grid grid-cols-1 gap-4 lg:grid-cols-3">
          {worlds.map((world, i) => {
            const Icon = world.icon;
            return (
              <li key={world.title}>
                <Reveal delay={i * 0.08} className="h-full">
                  <SpotlightCard as="article" className="flex h-full flex-col overflow-hidden p-0">
                    <div className="flex flex-1 flex-col p-6">
                      <div className="flex items-center justify-between gap-3">
                        <span className="grid size-11 place-items-center rounded-xl border border-brand/30 bg-brand/10">
                          <Icon className="size-5 text-brand-300" aria-hidden />
                        </span>
                        <span className="rounded-full border border-white/10 px-2.5 py-1 text-center font-mono text-[10px] uppercase tracking-[0.12em] text-brand-300">
                          {world.tab}
                        </span>
                      </div>
                      <p className="mt-6 font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
                        {world.kicker}
                      </p>
                      <h3 className="mt-2 text-xl font-semibold tracking-tight text-white">{world.title}</h3>
                      <div className="mt-4 space-y-3">
                        {world.paragraphs.map((paragraph) => (
                          <p key={paragraph} className="text-sm leading-relaxed text-muted">
                            {paragraph}
                          </p>
                        ))}
                      </div>
                    </div>
                    <p className="border-t border-brand/20 bg-brand/[0.06] px-6 py-4 text-sm font-medium text-white">
                      {world.result}
                    </p>
                  </SpotlightCard>
                </Reveal>
              </li>
            );
          })}
        </ul>
      </div>
    </Section>
  );
}
