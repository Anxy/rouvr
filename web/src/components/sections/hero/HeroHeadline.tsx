"use client";

import { m } from "motion/react";
import { cn } from "@/lib/utils";

/** Splits the headline into words; words after `highlightFrom` get the shimmer gradient.
 *  A newline in `text` is a hard break, so each line stays on its own row. */
export function HeroHeadline({
  text,
  highlightFrom,
  className,
}: {
  text: string;
  highlightFrom: number;
  className?: string;
}) {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const words = lines.flatMap((line) => line.split(/\s+/));

  return (
    <h1 className={cn(lines.length === 1 && "text-balance", className)}>
      {/* Real text for crawlers and screen readers; animation is decorative. */}
      <span className="sr-only">{words.join(" ")}</span>
      <span aria-hidden="true">
        {lines.map((line, lineIndex) => {
          const lineWords = line.split(/\s+/);
          const start = lines
            .slice(0, lineIndex)
            .reduce((count, previous) => count + previous.split(/\s+/).length, 0);

          return (
            <span key={line} className={lines.length > 1 ? "block" : undefined}>
              {lineWords.map((word, offset) => {
                const i = start + offset;
                return (
                  <span
                    key={`${word}-${i}`}
                    className="inline-block overflow-hidden pb-[0.12em] align-top"
                  >
                    <m.span
                      className={cn(
                        "inline-block",
                        i >= highlightFrom && "text-gradient md:animate-shimmer",
                      )}
                      initial={{ y: "105%", opacity: 0 }}
                      animate={{ y: "0%", opacity: 1 }}
                      transition={{
                        duration: 0.8,
                        delay: 0.1 + i * 0.06,
                        ease: [0.22, 1, 0.36, 1],
                      }}
                    >
                      {word}
                    </m.span>
                    {offset < lineWords.length - 1 && <span>&nbsp;</span>}
                  </span>
                );
              })}
            </span>
          );
        })}
      </span>
    </h1>
  );
}
