import { cn } from "@/lib/utils";

export function Eyebrow({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex w-fit max-w-full items-center gap-2 rounded-full border border-brand/25 bg-brand/[0.07] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-brand-300 sm:text-[11px] sm:tracking-[0.18em]",
        className,
      )}
    >
      <span className="size-1.5 shrink-0 rounded-full bg-brand animate-pulse-dot" aria-hidden />
      {children}
    </span>
  );
}
