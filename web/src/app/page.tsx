import type { Metadata } from "next";
import { Hero } from "@/components/sections/Hero";
import { ProblemSection } from "@/components/sections/ProblemSection";
import { CapabilitiesSection } from "@/components/sections/CapabilitiesSection";
import { ApproachSection } from "@/components/sections/ApproachSection";
import { HowItWorksSection } from "@/components/sections/HowItWorksSection";
import { UseCasesSection } from "@/components/sections/UseCasesSection";
import { EvidenceSection } from "@/components/sections/EvidenceSection";
import { AudienceSection } from "@/components/sections/AudienceSection";
import { FinalCTA } from "@/components/sections/FinalCTA";
import { control } from "@/content/home";
import {
  SITE_DESCRIPTION,
  SITE_TITLE,
  jsonLdScript,
  organizationJsonLd,
  softwareApplicationJsonLd,
  websiteJsonLd,
} from "@/lib/seo";

export const metadata: Metadata = {
  title: { absolute: SITE_TITLE },
  description: SITE_DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: {
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    url: "/",
  },
  twitter: {
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
};

export default function Home() {
  const jsonLd = [organizationJsonLd(), websiteJsonLd(), softwareApplicationJsonLd()];

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLdScript(jsonLd) }}
      />
      <Hero />
      <ProblemSection />
      <HowItWorksSection />
      <ApproachSection />
      <CapabilitiesSection />
      <UseCasesSection />
      <AudienceSection id="continuity" />
      <AudienceSection id="trust" content={control} />
      <EvidenceSection />
      <FinalCTA />
    </>
  );
}
