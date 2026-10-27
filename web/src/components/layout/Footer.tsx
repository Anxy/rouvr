import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import { GridBackground } from "@/components/effects/GridBackground";
import { Container } from "@/components/ui/Container";
import { footerLinks } from "@/content/nav";

export function Footer() {
  return (
    <footer className="relative overflow-hidden border-t border-white/10 bg-abyss">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand to-transparent" />
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px overflow-hidden">
        <span className="absolute inset-y-0 left-0 h-px w-1/3 bg-gradient-to-r from-transparent via-brand-300 to-transparent motion-safe:animate-header-scan" />
      </div>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-64"
        style={{
          background:
            "radial-gradient(ellipse 80% 80% at 18% 0%, rgb(8 169 240 / 0.28), transparent 62%), radial-gradient(ellipse 50% 70% at 80% 0%, rgb(8 169 240 / 0.12), transparent 70%)",
        }}
      />
      <GridBackground className="opacity-50" />

      <Container className="@container relative py-14 sm:py-16">
        <div className="flex flex-col gap-8 sm:flex-row sm:items-center sm:justify-between">
          <Logo />
          <nav aria-label="Footer">
            <ul className="flex flex-wrap gap-2">
              {footerLinks.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="inline-flex rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-2 text-[13px] tracking-[0.08em] text-white/80 shadow-[inset_0_1px_0_rgb(255_255_255/0.04)] transition duration-300 hover:-translate-y-0.5 hover:border-brand/50 hover:bg-brand/10 hover:text-white hover:shadow-[0_0_22px_-10px_rgb(8_169_240/0.9)]"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </div>
        <p className="mt-8 max-w-none pb-1 font-semibold leading-[0.92] tracking-[-0.045em] text-white [font-size:clamp(3rem,7.5cqi,8.75rem)]">
          Make it happen.
        </p>
        <p className="mt-4 text-sm text-muted sm:text-lg">Your pack, your ambition.</p>
      </Container>

      <Container className="relative flex flex-col items-start justify-between gap-3 border-t border-white/10 py-6 text-xs text-muted sm:flex-row sm:items-center">
        <p>&copy; {new Date().getFullYear()} Rouvr. All rights reserved.</p>
        <p className="flex gap-4">
          <a href="mailto:hello@rouvr.ai" className="transition-colors hover:text-white">
            hello@rouvr.ai
          </a>
          <Link href="/privacy" className="transition-colors hover:text-white">
            Privacy
          </Link>
          <Link href="/terms" className="transition-colors hover:text-white">
            Terms
          </Link>
        </p>
      </Container>
    </footer>
  );
}
