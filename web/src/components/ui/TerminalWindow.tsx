import { cn } from "@/lib/utils";

export function TerminalWindow({
  title,
  meta,
  className,
  bodyClassName,
  children,
}: {
  title: string;
  meta?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "glow-border overflow-hidden rounded-2xl bg-navy/90 shadow-[0_30px_80px_-30px_rgb(8_169_240/0.35)]",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b border-steel/50 bg-abyss/60 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex shrink-0 gap-1.5" aria-hidden>
            <span className="size-2.5 rounded-full bg-steel" />
            <span className="size-2.5 rounded-full bg-steel" />
            <span className="size-2.5 rounded-full bg-brand/70" />
          </div>
          <span className="truncate font-mono text-xs text-muted">{title}</span>
        </div>
        {meta}
      </div>
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </div>
  );
}
