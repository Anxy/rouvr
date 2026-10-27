import Link from "next/link";
import { cn } from "@/lib/utils";
import { Mascot } from "./Mascot";

export function Logo({ className, withMark = true }: { className?: string; withMark?: boolean }) {
  return (
    <Link
      href="/"
      aria-label="Rouvr home"
      className={cn("group inline-flex items-center gap-2.5", className)}
    >
      {withMark && (
        <Mascot className="size-8 transition-transform duration-500 group-hover:-rotate-6" decorative />
      )}
      <span className="text-xl font-semibold tracking-tight text-white">
        rouvr<span className="text-brand">.</span>
      </span>
    </Link>
  );
}
