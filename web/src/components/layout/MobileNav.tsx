"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AnimatePresence, m, useReducedMotion } from "motion/react";
import { ArrowUpRight, Menu, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { navLinks } from "@/content/nav";
import { cn } from "@/lib/utils";

export function MobileNav({
  open,
  onOpenChange,
  panel = false,
  inset = false,
  active = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  panel?: boolean;
  inset?: boolean;
  active?: string | null;
}) {
  const reduce = useReducedMotion();
  const close = () => onOpenChange(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onOpenChange(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (panel) {
    return (
      <AnimatePresence>
        {open && (
          <m.nav
            id="mobile-menu"
            aria-label="Mobile"
            data-lenis-prevent
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: reduce ? 0.15 : 0.35, ease: [0.22, 1, 0.36, 1] }}
            className={cn(
              "absolute top-full z-50 mt-2 origin-top lg:hidden",
              inset ? "inset-x-3 sm:inset-x-4" : "inset-x-0",
            )}
          >
            <div className="rounded-3xl border border-white/15 bg-abyss/90 p-6 shadow-[0_24px_60px_-24px_rgb(8_169_240/0.45)] backdrop-blur-xl">
              <ul className="flex flex-col">
                {navLinks.map((link, i) => {
                  const on = active === link.href;
                  return (
                    <m.li
                      key={link.href}
                      initial={reduce ? false : { opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 0.04 + i * 0.04, duration: 0.3 }}
                    >
                      <Link
                        href={link.href}
                        onClick={close}
                        className={cn(
                          "flex items-center justify-between border-b border-white/10 py-3.5 text-sm tracking-[0.14em] transition-colors hover:text-white",
                          on ? "text-brand-300" : "text-white/80",
                        )}
                      >
                        {link.label}
                        <span className="font-mono text-[10px] text-muted">0{i + 1}</span>
                      </Link>
                    </m.li>
                  );
                })}
              </ul>
              <Button href="/contact" size="lg" onClick={close} className="mt-5 w-full">
                Get in touch
                <ArrowUpRight className="size-4" />
              </Button>
            </div>
          </m.nav>
        )}
      </AnimatePresence>
    );
  }

  return (
    <div className="lg:hidden">
      <button
        type="button"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        aria-controls="mobile-menu"
        onClick={() => onOpenChange(!open)}
        className="relative z-[60] grid size-10 place-items-center rounded-full border border-white/20 text-white transition-colors duration-300 hover:border-brand/60 hover:bg-white/10"
      >
        {open ? <X className="size-5" /> : <Menu className="size-5" />}
      </button>
    </div>
  );
}
