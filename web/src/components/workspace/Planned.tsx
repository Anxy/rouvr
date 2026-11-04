import Link from "next/link";

// Planned shell: every nav destination the dashboard package has not built
// yet renders this instead of a dead button. Labelled honestly ("Planned,
// not built yet"), navigates only through working Links — this component
// renders no buttons at all.
export function Planned({
  title,
  blurb,
  actionHref,
  actionLabel,
}: {
  title: string;
  blurb: string;
  actionHref: string;
  actionLabel: string;
}) {
  return (
    <div data-planned-shell={title}>
      <p className="font-mono text-[10px] uppercase tracking-widest text-cyan2">
        Planned
      </p>
      <h1 className="mt-2 text-2xl font-semibold text-white">{title}</h1>
      <p className="mt-2 text-sm text-muted">not built yet</p>
      <p className="mt-1 max-w-xl text-sm text-dim">{blurb}</p>
      <div className="mt-6">
        <Link
          href={actionHref}
          className="inline-flex h-10 items-center rounded-full bg-brand px-5 text-sm font-medium text-abyss transition-colors hover:bg-cyan2"
        >
          {actionLabel}
        </Link>
      </div>
    </div>
  );
}
