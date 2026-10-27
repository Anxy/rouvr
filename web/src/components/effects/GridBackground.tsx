import { cn } from "@/lib/utils";

export function GridBackground({
  variant = "grid",
  className,
}: {
  variant?: "grid" | "dot";
  className?: string;
}) {
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-0 mask-radial",
        variant === "grid" ? "grid-bg" : "dot-bg",
        className,
      )}
    />
  );
}
