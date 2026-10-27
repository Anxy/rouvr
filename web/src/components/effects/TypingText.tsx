"use client";

import { useEffect, useRef, useState } from "react";
import { useInView, useReducedMotion } from "motion/react";

export function TypingText({
  text,
  speed = 28,
  startDelay = 300,
  className,
  onDone,
  cursor = true,
}: {
  text: string;
  speed?: number;
  startDelay?: number;
  className?: string;
  onDone?: () => void;
  cursor?: boolean;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const reduce = useReducedMotion();
  const [count, setCount] = useState(0);
  const visibleCount = reduce ? text.length : count;

  useEffect(() => {
    if (!inView || !reduce) return;
    onDone?.();
    // onDone is a caller-owned callback and is not part of the animation clock.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, reduce, text]);

  useEffect(() => {
    if (!inView || reduce) return;
    let i = 0;
    let interval: ReturnType<typeof setInterval>;
    const start = setTimeout(() => {
      interval = setInterval(() => {
        i += 1;
        setCount(i);
        if (i >= text.length) {
          clearInterval(interval);
          onDone?.();
        }
      }, speed);
    }, startDelay);
    return () => {
      clearTimeout(start);
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, reduce, text, speed, startDelay]);

  const done = visibleCount >= text.length;

  return (
    <span ref={ref} className={className}>
      <span className="sr-only">{text}</span>
      <span aria-hidden>
        {text.slice(0, visibleCount)}
        {cursor && (
          <span
            className={
              done
                ? "ml-0.5 inline-block h-[1em] w-[0.5ch] translate-y-[0.15em] bg-brand/80 animate-blink"
                : "ml-0.5 inline-block h-[1em] w-[0.5ch] translate-y-[0.15em] bg-brand/80"
            }
          />
        )}
      </span>
    </span>
  );
}
