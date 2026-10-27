import type { Metadata } from "next";
import { LegalPage } from "@/components/layout/LegalPage";
import { breadcrumbJsonLd, jsonLdScript } from "@/lib/seo";
import { CONTACT_EMAIL } from "@/lib/utils";

const title = "Terms of Use";
const description = "Terms governing use of the Rouvr website.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: "/terms" },
  openGraph: { title, description, url: "/terms" },
  twitter: { title, description },
};

export default function TermsPage() {
  const jsonLd = breadcrumbJsonLd([
    { name: "Home", path: "/" },
    { name: "Terms of Use", path: "/terms" },
  ]);

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLdScript(jsonLd) }}
      />
    <LegalPage
      eyebrow="Terms"
      title="Terms of Use"
      description="Terms governing use of the Rouvr website and messages you send through it."
      updated="October 16, 2026"
      sections={[
        {
          heading: "Acceptance of terms",
          paragraphs: [
            "By using rouvr.si or contacting us through the site, you agree to these terms. If you do not agree, do not use the site.",
          ],
        },
        {
          heading: "The website",
          paragraphs: [
            "Rouvr puts a pack of AI specialists to work on what you want to achieve. This website describes that and lets you get in touch.",
            "A message through the contact form does not guarantee a particular feature set, date or level of availability. Descriptions on this site are informational.",
          ],
        },
        {
          heading: "Eligibility",
          paragraphs: [
            "You must be able to form a binding agreement. Use the site for a professional or organizational purpose, and do not submit information you are not allowed to share.",
          ],
        },
        {
          heading: "Accurate information",
          paragraphs: [
            "The website does not create a product account. You agree that the details you submit are accurate. We may decline a request that is incomplete or misleading.",
          ],
        },
        {
          heading: "Acceptable use",
          paragraphs: [
            "Do not misuse the site. That includes unlawful activity, attempts to disrupt the service, automated access that degrades the site, and malicious content submitted through the forms.",
          ],
        },
        {
          heading: "Intellectual property",
          paragraphs: [
            "The Rouvr name, site design and content on this website are owned by Rouvr or its licensors. You may not copy them for a competing product or remove proprietary notices.",
            "You keep ownership of the information you submit. You give us permission to use it to respond to you.",
          ],
        },
        {
          heading: "Disclaimers",
          paragraphs: [
            "The website is provided as available and may change or be interrupted. We do not promise uninterrupted access or a specific engineering result.",
          ],
        },
        {
          heading: "Limitation of liability",
          paragraphs: [
            "To the extent permitted by law, Rouvr is not liable for indirect, incidental or consequential damages arising from use of the website. Nothing in these terms limits liability that cannot legally be limited.",
          ],
        },
        {
          heading: "Termination",
          paragraphs: [
            "We may suspend access to the website if these terms are broken. You may ask us to remove a message you sent by emailing " +
              CONTACT_EMAIL +
              ".",
          ],
        },
        {
          heading: "Governing law",
          paragraphs: [
            "The specific governing law and venue for disputes will be published here once confirmed. Rights that the law of your country requires us to honor still apply.",
          ],
        },
        {
          heading: "Contact",
          paragraphs: ["Questions about these terms: " + CONTACT_EMAIL + "."],
        },
      ]}
    />
    </>
  );
}
