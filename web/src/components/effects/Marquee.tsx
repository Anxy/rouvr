import { cn } from "@/lib/utils";
import { PauseOffscreen } from "./PauseOffscreen";

export function Marquee({
  items,
  className,
}: {
  items: string[];
  className?: string;
}) {
  const row = [...items, ...items];
  return (
    <PauseOffscreen className={cn("relative overflow-hidden mask-fade-x", className)}>
      <ul className="flex w-max animate-marquee gap-10 will-change-transform hover:[animation-play-state:paused]">
        {row.map((item, i) => (
          <li
            key={`${item}-${i}`}
            aria-hidden={i >= items.length}
            className="flex items-center gap-10 whitespace-nowrap font-mono text-xs uppercase tracking-[0.2em] text-muted/80"
          >
            {item}
            <span className="size-1 rounded-full bg-brand/60" aria-hidden />
          </li>
        ))}
      </ul>
    </PauseOffscreen>
  );
}
