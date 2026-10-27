"use client";

import { AnimatePresence, m } from "motion/react";

export const fieldClass =
  "w-full rounded-xl border border-steel/70 bg-abyss/70 px-4 py-3 text-[15px] text-white placeholder:text-muted/60 transition-colors duration-200 hover:border-steel-400 focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15";

export function FieldError({ id, message }: { id: string; message?: string }) {
  return (
    <AnimatePresence initial={false}>
      {message && (
        <m.p
          id={id}
          role="alert"
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          className="mt-2 text-sm text-danger"
        >
          {message}
        </m.p>
      )}
    </AnimatePresence>
  );
}
