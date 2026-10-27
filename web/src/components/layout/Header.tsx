"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { m, useMotionValue, useMotionValueEvent, useReducedMotion, useScroll, useTransform } from "motion/react";
import { ArrowUpRight } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { Button } from "@/components/ui/Button";
import { navLinks } from "@/content/nav";
import { cn } from "@/lib/utils";
import { MobileNav } from "./MobileNav";
import { ThemeToggle } from "./ThemeToggle";

const homeSections = navLinks
  .map((link) => (link.href.startsWith("/#") ? link.href.slice(2) : null))
  .filter((id): id is string => id !== null);

/** A section counts as current once its heading clears the header. */
const SECTION_LINE = 112;
/** Scroll distance over which the bar eases into the floating pill. */
const MORPH_DISTANCE = 110;

function smoothstep(value: number) {
  const t = Math.min(1, Math.max(0, value));
  return t * t * (3 - 2 * t);
}

/** Inset and glass arrive after the corners have already rounded. */
function settle(progress: number) {
  return smoothstep((progress - 0.15) / 0.85);
}

export function Header() {
  const { scrollY } = useScroll();
  const reduce = useReducedMotion();
  const side = useMotionValue(12);
  const morph = useTransform(scrollY, (y) => smoothstep(y / MORPH_DISTANCE));
  const marginTop = useTransform(morph, (p) => 12 * settle(p));
  const marginX = useTransform<number, number>([morph, side], ([p, s]) => settle(p) * s);
  const radius = useTransform(morph, (p) => 40 * Math.min(1, p / 0.42));
  const padY = useTransform(morph, (p) => 20 - 12 * settle(p));
  const washOpacity = useTransform(morph, (p) => 1 - Math.min(1, Math.max(0, (p - 0.05) / 0.7)));
  const backgroundColor = useTransform(morph, (p) => {
    const formed = Math.min(1, p / 0.42);
    const alpha = (84 * formed).toFixed(1);
    const sheen = (8 * formed).toFixed(1);
    return `linear-gradient(180deg, color-mix(in srgb, var(--header-sheen) ${sheen}%, transparent), transparent 42%), color-mix(in srgb, var(--header-bar) ${alpha}%, transparent)`;
  });
  const backdropFilter = useTransform(morph, (p) => `blur(${(20 * settle(p)).toFixed(2)}px)`);
  const borderColor = useTransform(
    morph,
    (p) => `color-mix(in srgb, var(--header-line) ${(16 * Math.min(1, p / 0.42)).toFixed(1)}%, transparent)`,
  );
  const boxShadow = useTransform(morph, (p) => {
    const alpha = (settle(p) * 0.45).toFixed(3);
    return `0 22px 50px -20px rgba(8, 169, 240, ${alpha})`;
  });
  const scanOpacity = useTransform(morph, (p) => Math.max(0, (settle(p) - 0.8) / 0.2));
  const [pill, setPill] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [hash, setHash] = useState("");
  const [hover, setHover] = useState<string | null>(null);
  const [scrollActive, setScrollActive] = useState<string | null>(null);
  const pathname = usePathname();
  const routeKey = `${pathname}${hash}`;
  const [menuRoute, setMenuRoute] = useState(routeKey);
  if (menuRoute !== routeKey) {
    setMenuRoute(routeKey);
    setMenuOpen(false);
  }
  const active =
    pathname === "/contact" ? "/contact" : pathname !== "/" ? null : scrollActive;

  useEffect(() => {
    const read = () => setHash(window.location.hash);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, [pathname]);

  useEffect(() => {
    if (pathname !== "/") return;

    let frame = 0;
    const update = () => {
      frame = 0;
      const nearEnd =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 48;
      let current = "/#meet";
      if (nearEnd) {
        current = `/#${homeSections[homeSections.length - 1]}`;
      } else {
        for (const id of homeSections) {
          const el = document.getElementById(id);
          if (!el) continue;
          if (el.getBoundingClientRect().top <= SECTION_LINE) current = `/#${id}`;
        }
      }
      setScrollActive((prev) => (prev === current ? prev : current));
    };
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(update);
    };

    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [pathname]);

  useMotionValueEvent(morph, "change", (p) => {
    const next = p > 0.85;
    setPill((current) => (current === next ? current : next));
  });

  useEffect(() => {
    const xl = window.matchMedia("(min-width: 1280px)");
    const md = window.matchMedia("(min-width: 768px)");
    const read = () => side.set(xl.matches ? 48 : md.matches ? 32 : 12);
    read();
    xl.addEventListener("change", read);
    md.addEventListener("change", read);
    return () => {
      xl.removeEventListener("change", read);
      md.removeEventListener("change", read);
    };
  }, [side]);

  const lit = hover ?? active;

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50">
      <m.div
        aria-hidden
        style={{ opacity: washOpacity }}
        className="absolute inset-x-0 top-0 h-44 bg-gradient-to-b from-abyss/90 via-abyss/35 to-transparent"
      />
      <m.div
        style={{
          marginTop,
          marginLeft: marginX,
          marginRight: marginX,
          borderRadius: radius,
          paddingTop: padY,
          paddingBottom: padY,
          background: backgroundColor,
          borderColor,
          backdropFilter,
          WebkitBackdropFilter: backdropFilter,
          boxShadow,
        }}
        className="pointer-events-auto relative border"
      >
        <div className="flex items-center justify-between gap-4 px-4 sm:px-6">
          <Logo className="origin-left transition-transform duration-300 hover:scale-[1.03]" />

          <nav
            aria-label="Primary"
            className="hidden min-w-0 lg:block"
            onMouseLeave={() => setHover(null)}
          >
            <ul className="flex items-center gap-1">
              {navLinks.map((link) => {
                const on = lit === link.href;
                return (
                  <li key={link.href} onMouseEnter={() => setHover(link.href)}>
                    <Link
                      href={link.href}
                      aria-current={active === link.href ? "true" : undefined}
                      className={cn(
                        "relative inline-flex whitespace-nowrap px-3 py-2 text-[13px] tracking-[0.12em] transition-colors duration-300",
                        on ? "text-white" : "text-white/75 hover:text-white",
                      )}
                    >
                      {on &&
                        (reduce ? (
                          <span className="absolute inset-0 rounded-full border border-brand/50 bg-brand/10" />
                        ) : (
                          <m.span
                            layoutId="nav-pill"
                            className="absolute inset-0 rounded-full border border-brand/45 bg-brand/10 shadow-[0_0_22px_-8px_rgb(8_169_240/0.95)]"
                            transition={{ type: "spring", stiffness: 420, damping: 34 }}
                          />
                        ))}
                      <span className="relative transition-transform duration-300 hover:-translate-y-0.5">
                        {link.label}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Button href="/contact" size="md" className="hidden sm:inline-flex lg:hidden">
              Get in touch
              <ArrowUpRight className="size-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
            </Button>
            <MobileNav open={menuOpen} onOpenChange={setMenuOpen} active={active} />
          </div>
        </div>

        <m.div
          aria-hidden
          style={{ opacity: scanOpacity }}
          className="pointer-events-none absolute inset-x-8 bottom-0 h-px overflow-hidden"
        >
          <span className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-brand-300 to-transparent motion-safe:animate-header-scan" />
        </m.div>

        <MobileNav open={menuOpen} onOpenChange={setMenuOpen} panel inset={!pill} active={active} />
      </m.div>
    </header>
  );
}
