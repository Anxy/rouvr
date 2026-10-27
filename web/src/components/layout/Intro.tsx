"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { AnimatePresence, m } from "motion/react";
import { Mascot } from "@/components/brand/Mascot";

const INTRO_SEEN_KEY = "rouvr-intro-seen";

function markSeen() {
  try {
    sessionStorage.setItem(INTRO_SEEN_KEY, "1");
  } catch {
    /* private mode */
  }
}

function releaseIntroLock() {
  document.documentElement.classList.remove("intro-play");
}

function introSkipped() {
  try {
    if (sessionStorage.getItem(INTRO_SEEN_KEY) === "1") return true;
  } catch {
    /* private mode */
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function subscribeIntro(onStoreChange: () => void) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", onStoreChange);
  return () => media.removeEventListener("change", onStoreChange);
}

export function Intro() {
  // Present in the first HTML response. A head script adds `intro-play` before
  // paint on a fresh visit so the splash covers the page immediately.
  const skipped = useSyncExternalStore(subscribeIntro, introSkipped, () => false);
  const [dismissed, setDismissed] = useState(false);
  const show = !skipped && !dismissed;

  useEffect(() => {
    if (skipped) {
      releaseIntroLock();
      return;
    }
    if (dismissed) return;

    document.documentElement.classList.add("intro-play");

    const block = (event: Event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    window.addEventListener("wheel", block, { capture: true, passive: false });
    window.addEventListener("touchmove", block, { capture: true, passive: false });

    const timer = window.setTimeout(() => {
      setDismissed(true);
      markSeen();
    }, 1600);

    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("wheel", block, { capture: true });
      window.removeEventListener("touchmove", block, { capture: true });
    };
  }, [dismissed, skipped]);

  const dismiss = () => {
    setDismissed(true);
    markSeen();
  };

  return (
    <AnimatePresence onExitComplete={releaseIntroLock}>
      {show && (
        <m.button
          type="button"
          data-intro-overlay
          aria-label="Skip intro"
          onClick={dismiss}
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
          className="fixed inset-0 z-[80] flex cursor-pointer flex-col items-center justify-center bg-abyss"
        >
          <m.span
            aria-hidden
            className="pointer-events-none absolute size-56 rounded-full border border-brand/50"
            initial={{ scale: 0.4, opacity: 0.9 }}
            animate={{ scale: 1.8, opacity: 0 }}
            transition={{ duration: 1.1, ease: [0.22, 1, 0.36, 1] }}
          />
          <m.span
            aria-hidden
            className="pointer-events-none absolute size-40 rounded-full bg-brand/25 blur-3xl"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5 }}
          />
          <m.span
            initial={{ opacity: 0, scale: 0.72 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: "spring", stiffness: 280, damping: 20 }}
          >
            <Mascot decorative className="size-24 drop-shadow-[0_12px_30px_rgb(8_169_240/0.55)]" />
          </m.span>
          <m.span
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.22, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className="mt-6 text-3xl font-semibold tracking-tight text-white"
          >
            rouvr<span className="text-brand">.</span>
          </m.span>
        </m.button>
      )}
    </AnimatePresence>
  );
}
