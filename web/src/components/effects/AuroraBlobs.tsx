import { cn } from "@/lib/utils";
import { PauseOffscreen } from "./PauseOffscreen";

/**
 * Blurred gradient blobs. The blur is static (radial-gradient falloff, no filter),
 * and only transform is animated, so this stays on the compositor.
 */
export function AuroraBlobs({ className }: { className?: string }) {
  return (
    <PauseOffscreen
      className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}
    >
      <div
        aria-hidden
        className="absolute -left-[15%] -top-[25%] h-[70vmax] w-[70vmax] animate-drift-a rounded-full will-change-transform"
        style={{
          background:
            "radial-gradient(circle at center, rgb(8 169 240 / 0.22), rgb(8 169 240 / 0.06) 40%, transparent 68%)",
        }}
      />
      <div
        aria-hidden
        className="absolute -right-[20%] top-[5%] hidden h-[60vmax] w-[60vmax] animate-drift-b rounded-full will-change-transform sm:block"
        style={{
          background:
            "radial-gradient(circle at center, rgb(56 120 255 / 0.16), rgb(36 68 94 / 0.08) 45%, transparent 70%)",
        }}
      />
    </PauseOffscreen>
  );
}
