import type { Metadata } from "next";
import { LegalPage } from "@/components/layout/LegalPage";
import { breadcrumbJsonLd, jsonLdScript } from "@/lib/seo";
import { CONTACT_EMAIL } from "@/lib/utils";

const title = "Privacy Policy";
const description = "How Rouvr collects, uses and protects personal information.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: "/privacy" },
  openGraph: { title, description, url: "/privacy" },
  twitter: { title, description },
};

export default function PrivacyPage() {
  const jsonLd = breadcrumbJsonLd([
    { name: "Home", path: "/" },
    { name: "Privacy Policy", path: "/privacy" },
  ]);

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLdScript(jsonLd) }}
      />
    <LegalPage
      eyebrow="Privacy"
      title="Privacy Policy"
      description="How Rouvr collects, uses and protects personal information submitted through this website."
      updated="October 16, 2026"
      sections={[
        {
          heading: "Who we are",
          paragraphs: [
            "Rouvr operates the website at rouvr.si. This policy explains how we handle personal information collected through the site, including the contact form.",
            "The registered legal entity and postal address will be added here once they are confirmed. Until then, privacy requests can be sent to " +
              CONTACT_EMAIL +
              ".",
          ],
        },
        {
          heading: "Information we collect",
          paragraphs: [
            "We collect the information you choose to send us, and limited technical data needed to operate the site.",
          ],
          bullets: [
            "Identity and contact details: name, email address, and optional role or organisation.",
            "What you tell us you would hand over to rouvr first.",
            "Communications you send to us by email.",
            "Usage data such as pages visited, referring URLs, browser type and approximate location derived from an IP address, if analytics are enabled.",
          ],
        },
        {
          heading: "How we use information",
          paragraphs: ["We use personal information to:"],
          bullets: [
            "Reply to messages you send us.",
            "Understand how the website is used and improve it.",
            "Protect the site from abuse, including automated submissions.",
          ],
        },
        {
          heading: "Legal bases",
          paragraphs: [
            "Where the GDPR or a similar law applies, we rely on steps you request when you ask us to respond, and on legitimate interests in securing and improving the website.",
          ],
        },
        {
          heading: "Sharing and processors",
          paragraphs: [
            "We do not sell personal information. We share it with service providers that host the site, deliver email or store form submissions, and only as needed for those services. We may also disclose information when the law requires it.",
            "A list of processors will be published once those vendors are confirmed.",
          ],
        },
        {
          heading: "Retention",
          paragraphs: [
            "We keep contact details for as long as needed to manage the request and any follow-up, then delete or anonymize them. Specific retention periods will be confirmed and published here.",
          ],
        },
        {
          heading: "International transfers",
          paragraphs: [
            "If information is processed outside your country, we will use a transfer mechanism appropriate to that transfer. The hosting and email providers, and the safeguards that apply, will be described here once they are confirmed.",
          ],
        },
        {
          heading: "Your rights",
          paragraphs: [
            "Depending on where you live, you may request access, correction, deletion, restriction or a copy of your personal information, and you may object to certain processing or withdraw consent. Email " +
              CONTACT_EMAIL +
              " with the subject “Privacy request”. You may also lodge a complaint with your local data protection authority.",
          ],
        },
        {
          heading: "Cookies and analytics",
          paragraphs: [
            "The site may use cookies that are required for pages to function. If we add analytics or similar cookies, we will describe them here and, where the law requires it, ask for consent before they run.",
          ],
        },
        {
          heading: "Contact for privacy requests",
          paragraphs: [
            "Send privacy requests to " + CONTACT_EMAIL + ". Include the email address you used on the form so we can find the right record.",
          ],
        },
        {
          heading: "Policy updates",
          paragraphs: [
            "We will update this policy if our practices change. The date at the top of this page is the current version. Material changes will be posted here.",
          ],
        },
      ]}
    />
    </>
  );
}
