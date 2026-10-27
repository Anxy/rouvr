import { cn } from "@/lib/utils";

export function Badge({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-steel/70 bg-navy/70 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}
