"use client";

import { useRef } from "react";
import { cn } from "@/lib/utils";

/**
 * Card with a cursor-following glow. Pointer position is written to CSS variables
 * inside requestAnimationFrame, so moving the mouse never triggers a React render.
 */
export function SpotlightCard({
  className,
  children,
  as: Tag = "div",
}: {
  className?: string;
  children: React.ReactNode;
  as?: "div" | "article" | "li";
}) {
  const ref = useRef<HTMLElement>(null);
  const frame = useRef(0);

  const onMove = (e: React.PointerEvent) => {
    if (e.pointerType !== "mouse") return;
    const el = ref.current;
    if (!el) return;
    const { clientX, clientY } = e;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${clientX - r.left}px`);
      el.style.setProperty("--my", `${clientY - r.top}px`);
    });
  };

  return (
    <Tag
      ref={ref as React.Ref<never>}
      onPointerMove={onMove}
      onPointerEnter={(e) => {
        if (e.pointerType === "mouse") ref.current?.style.setProperty("--spot", "1");
      }}
      onPointerLeave={() => ref.current?.style.setProperty("--spot", "0")}
      className={cn(
        "spotlight glow-border rounded-2xl bg-navy/60 transition-transform duration-500 ease-out hover:-translate-y-1",
        className,
      )}
    >
      {children}
    </Tag>
  );
}
