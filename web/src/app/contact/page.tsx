import type { Metadata } from "next";
import { AuroraBlobs } from "@/components/effects/AuroraBlobs";
import { GridBackground } from "@/components/effects/GridBackground";
import { Reveal } from "@/components/effects/Reveal";
import { ContactForm } from "@/components/forms/ContactForm";
import { Container } from "@/components/ui/Container";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { breadcrumbJsonLd, jsonLdScript } from "@/lib/seo";

const title = "Get in touch";
const description =
  "Tell us what you want to make happen, and what keeps getting in the way. Let's explore where your pack could help.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: "/contact" },
  openGraph: {
    title,
    description,
    url: "/contact",
  },
  twitter: {
    title,
    description,
  },
};

export default function ContactPage() {
  const jsonLd = breadcrumbJsonLd([
    { name: "Home", path: "/" },
    { name: "Contact", path: "/contact" },
  ]);

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLdScript(jsonLd) }}
      />
      <section className="relative isolate overflow-hidden pb-24 pt-32 sm:pb-32 sm:pt-40">
        <div aria-hidden className="absolute inset-0 -z-10 bg-navy" />
        <AuroraBlobs className="-z-10" />
        <GridBackground className="-z-10 opacity-50" />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 -z-10 h-32 bg-gradient-to-b from-transparent to-abyss"
        />

        <Container className="grid grid-cols-1 items-start gap-14 lg:grid-cols-2 lg:gap-20">
          <div>
            <Reveal>
              <Eyebrow>Get in touch</Eyebrow>
            </Reveal>
            <Reveal delay={0.05}>
              <h1 className="mt-6 text-balance text-4xl font-semibold leading-[1.05] tracking-tight text-white sm:text-6xl">
                What would you do today with rouvr?
              </h1>
            </Reveal>
            <Reveal delay={0.1}>
              <p className="mt-6 max-w-lg text-pretty leading-relaxed text-muted sm:text-lg">
                The business you&apos;re building. The work that keeps spilling into your evening. That
                gloriously unreasonable idea.
              </p>
            </Reveal>
            <Reveal delay={0.15}>
              <p className="mt-4 max-w-lg text-pretty leading-relaxed text-muted sm:text-lg">
                Tell us what you want to make happen, and what keeps getting in the way. Let&apos;s explore
                where your pack could help.
              </p>
            </Reveal>
            <Reveal delay={0.2}>
              <p className="mt-8 text-sm font-medium text-brand-300">Your pack. Go on, plot bigger.</p>
              <p className="mt-6 text-xl font-semibold text-white">Rouvr. Make it happen.</p>
              <p className="mt-2 text-muted">Your pack, your ambition.</p>
            </Reveal>
          </div>

          <Reveal delay={0.15} y={28}>
            <div className="glow-border rounded-3xl bg-navy/80 p-6 shadow-[0_30px_80px_-30px_rgb(8_169_240/0.35)] backdrop-blur-sm sm:p-10">
              <ContactForm />
            </div>
          </Reveal>
        </Container>
      </section>
    </>
  );
}
