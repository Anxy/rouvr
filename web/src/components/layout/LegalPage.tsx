import { PageHero } from "@/components/layout/PageHero";
import { Container } from "@/components/ui/Container";

export type LegalSection = {
  heading: string;
  paragraphs: string[];
  bullets?: string[];
};

export function LegalPage({
  eyebrow,
  title,
  description,
  updated,
  sections,
}: {
  eyebrow: string;
  title: string;
  description: string;
  updated: string;
  sections: LegalSection[];
}) {
  return (
    <>
      <PageHero eyebrow={eyebrow} title={title} description={description} />
      <Container className="max-w-3xl pb-28">
        <p className="font-mono text-xs uppercase tracking-[0.18em] text-muted">Updated {updated}</p>
        <div className="mt-10 space-y-12">
          {sections.map((section, index) => (
            <section key={section.heading}>
              <h2 className="text-xl font-semibold tracking-tight text-white sm:text-2xl">
                <span className="mr-3 font-mono text-sm text-brand-300">
                  {String(index + 1).padStart(2, "0")}
                </span>
                {section.heading}
              </h2>
              <div className="mt-4 space-y-4 text-pretty leading-relaxed text-muted">
                {section.paragraphs.map((paragraph) => (
                  <p key={paragraph}>{paragraph}</p>
                ))}
                {section.bullets && (
                  <ul className="space-y-2">
                    {section.bullets.map((bullet) => (
                      <li key={bullet} className="flex gap-3">
                        <span className="mt-2 size-1.5 shrink-0 rounded-full bg-brand" aria-hidden />
                        <span>{bullet}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ))}
        </div>
      </Container>
    </>
  );
}
