import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function SectionLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <div className="mt-12 flex justify-center">
      <Link
        href={href}
        className="group inline-flex items-center gap-2 text-sm font-medium text-brand-300 transition-colors hover:text-white"
      >
        {children}
        <ArrowRight className="size-4 transition-transform duration-300 group-hover:translate-x-1" />
      </Link>
    </div>
  );
}
