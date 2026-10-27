"use client";

import { useRef } from "react";
import { m, useScroll, useSpring } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * A connector line that fills as the user scrolls through its section.
 * Uses scaleX/scaleY (transform) rather than width/height.
 */
export function BeamLine({
  orientation = "horizontal",
  className,
}: {
  orientation?: "horizontal" | "vertical";
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: orientation === "horizontal" ? ["start 85%", "start 35%"] : ["start 70%", "end 50%"],
  });
  const progress = useSpring(scrollYProgress, { stiffness: 120, damping: 30, mass: 0.4 });
  const horizontal = orientation === "horizontal";

  return (
    <div
      ref={ref}
      aria-hidden
      className={cn(
        "pointer-events-none overflow-hidden rounded-full bg-steel/50",
        horizontal ? "h-px w-full" : "h-full w-px",
        className,
      )}
    >
      <m.div
        style={horizontal ? { scaleX: progress } : { scaleY: progress }}
        className={cn(
          "size-full will-change-transform",
          horizontal
            ? "origin-left bg-gradient-to-r from-brand/0 via-brand to-brand-300"
            : "origin-top bg-gradient-to-b from-brand/0 via-brand to-brand-300",
        )}
      />
    </div>
  );
}
