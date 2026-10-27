import { cn } from "@/lib/utils";
import { PauseOffscreen } from "./PauseOffscreen";

/**
 * Animated light that travels around a border. A conic gradient is rotated
 * with transform only and clipped by the 1px padding gap.
 */
export function GlowBorder({
  className,
  innerClassName,
  children,
}: {
  className?: string;
  innerClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <PauseOffscreen
      className={cn("relative overflow-clip rounded-3xl p-px", className)}
    >
      <div
        aria-hidden
        className="absolute left-1/2 top-1/2 aspect-square w-[200%] -translate-x-1/2 -translate-y-1/2"
      >
        <div
          className="size-full animate-spin-slow will-change-transform"
          style={{
            background:
              "conic-gradient(from 0deg, transparent 0deg, rgb(8 169 240 / 0.9) 40deg, transparent 90deg, transparent 180deg, rgb(111 211 255 / 0.6) 220deg, transparent 270deg)",
          }}
        />
      </div>
      <div className="absolute inset-0 rounded-3xl border border-steel/60" aria-hidden />
      <div className={cn("relative rounded-[calc(1.5rem-1px)] bg-navy", innerClassName)}>
        {children}
      </div>
    </PauseOffscreen>
  );
}
