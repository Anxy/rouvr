import { Box, Eye, FlaskConical, PenLine, Rocket, ScanSearch, type LucideIcon } from "lucide-react";
import { Mascot } from "@/components/brand/Mascot";
import { PauseOffscreen } from "@/components/effects/PauseOffscreen";
import { Reveal } from "@/components/effects/Reveal";
import { SpotlightCard } from "@/components/effects/SpotlightCard";
import { Section, SectionHeading } from "@/components/ui/Section";
import { capabilities, pack } from "@/content/home";
import { cn } from "@/lib/utils";
import { CapabilityVisual } from "./capabilities/CapabilityVisual";

const roleIcons: Record<string, LucideIcon> = {
  Research: ScanSearch,
  Create: PenLine,
  Build: Box,
  Verify: FlaskConical,
  Deliver: Rocket,
  Operate: Eye,
};

const spans = ["lg:col-span-2", "", "", "lg:col-span-2", ""];

function FeatureCard({
  item,
  className,
}: {
  item: (typeof capabilities.items)[number];
  className?: string;
}) {
  const Icon = item.icon;
  return (
    <SpotlightCard as="article" className={cn("flex h-full flex-col p-5 sm:p-7", className)}>
      <div className="flex items-center gap-3">
        <span className="grid size-10 place-items-center rounded-xl border border-brand/30 bg-brand/10">
          <Icon className="size-5 text-brand-300" aria-hidden />
        </span>
        <h3 className="text-lg font-semibold text-white">{item.title}</h3>
      </div>
      <p className="mt-4 text-sm leading-relaxed text-muted">{item.body}</p>
      <div className="mt-auto w-full pt-6">
        <CapabilityVisual type={item.visual} />
      </div>
    </SpotlightCard>
  );
}

export function CapabilitiesSection() {
  const [first, second, ...rest] = capabilities.items;

  return (
    <Section id="capabilities" className="scroll-mt-20">
      <SectionHeading eyebrow={capabilities.eyebrow} title={capabilities.title} />

      <PauseOffscreen className="mt-12 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {[first, second].map((item, i) => (
          <Reveal key={item.title} delay={i * 0.08} className={cn("h-full", spans[i])}>
            <FeatureCard item={item} />
          </Reveal>
        ))}
      </PauseOffscreen>

      <Reveal className="relative mt-4 overflow-hidden rounded-3xl border border-white/10 bg-navy/50 p-5 shadow-[0_30px_80px_-48px_rgb(8_169_240/0.65)] sm:p-8">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-44 bg-[radial-gradient(ellipse_at_top,rgb(8_169_240/0.2),transparent_70%)]"
        />
        <div className="relative">
          <p className="text-lg font-semibold text-white sm:text-xl">{pack.title}</p>
          <div className="mt-6 flex items-center gap-4">
            <Mascot decorative className="size-11 shrink-0 drop-shadow-[0_8px_18px_rgb(8_169_240/0.45)]" />
            <div>
              <p className="text-lg font-semibold text-white">{pack.head}</p>
              <p className="mt-1 max-w-md text-sm text-muted">{pack.headBody}</p>
            </div>
          </div>
          <ol className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {pack.roles.map((role, index) => {
              const Icon = roleIcons[role.title];
              return (
                <li
                  key={role.title}
                  className="group flex gap-4 rounded-2xl border border-white/10 bg-abyss/40 p-4"
                >
                  <span className="grid size-10 shrink-0 place-items-center rounded-full border border-brand/50 bg-abyss text-brand-300 shadow-[0_0_18px_-6px_rgb(8_169_240/0.9)] transition duration-300 group-hover:border-brand group-hover:bg-brand group-hover:text-white">
                    {Icon && <Icon className="size-4" aria-hidden />}
                  </span>
                  <div>
                    <p className="font-semibold text-white">
                      <span className="mr-2 font-mono text-[11px] tracking-[0.12em] text-brand-300">
                        0{index + 1}
                      </span>
                      {role.title}
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-muted">{role.body}</p>
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="mt-5 grid grid-cols-1 gap-3 border-t border-white/10 pt-5 sm:grid-cols-2">
            {pack.base.map((line) => (
              <p key={line.label} className="text-sm leading-relaxed text-cloud">
                <span className="font-semibold text-white">{line.label}:</span> {line.body}
              </p>
            ))}
          </div>
          <p className="mt-4 text-sm leading-relaxed text-muted">{pack.caption}</p>
        </div>
      </Reveal>

      <PauseOffscreen className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {rest.map((item, i) => (
          <Reveal key={item.title} delay={i * 0.08}>
            <FeatureCard item={item} />
          </Reveal>
        ))}
      </PauseOffscreen>
    </Section>
  );
}
