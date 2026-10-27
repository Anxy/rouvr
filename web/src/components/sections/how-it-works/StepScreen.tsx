import { StatusPill } from "@/components/ui/StatusPill";

const mono = "font-mono text-[12px] leading-relaxed";

export function StepScreen({ index }: { index: number }) {
  switch (index) {
    case 0:
      return (
        <div className="space-y-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-brand-300">Think together</p>
          <div className="rounded-xl border border-brand/40 bg-abyss/70 p-4 text-sm leading-relaxed text-white shadow-[0_0_0_4px_rgb(8_169_240/0.08)]">
            Get today&apos;s report, check the figures, submit it, follow up on anything missing and let the team
            know.
            <span className="ml-0.5 inline-block h-4 w-2 translate-y-0.5 bg-brand/80 animate-blink" />
          </div>
          <div className="flex flex-wrap gap-2">
            {["report", "figures", "follow-ups"].map((tag) => (
              <span
                key={tag}
                className="rounded-md border border-steel bg-navy px-2 py-0.5 font-mono text-[11px] text-muted"
              >
                #{tag}
              </span>
            ))}
          </div>
        </div>
      );
    case 1:
      return (
        <div className="space-y-2">
          {[
            ["Outcome", "Today's report submitted", "agreed"],
            ["Checks", "Figures against source data", "required"],
            ["You", "Missing information", "comes back"],
          ].map(([id, title, state]) => (
            <div
              key={id}
              className="flex items-center gap-3 rounded-lg border border-steel/50 bg-abyss/60 px-3 py-2.5"
            >
              <span className="font-mono text-[11px] text-brand-300">{id}</span>
              <span className="flex-1 text-sm text-cloud">{title}</span>
              <span className="font-mono text-[10px] text-muted">{state}</span>
            </div>
          ))}
        </div>
      );
    default:
      return (
        <div className={`${mono} space-y-1.5 rounded-xl border border-steel/50 bg-abyss/80 p-4 text-muted`}>
          <p>
            <span className="text-brand-300">›</span> report retrieved
          </p>
          <p>
            <span className="text-brand-300">›</span> figures checked against source data
          </p>
          <p>
            <span className="text-brand-300">›</span> submitted through the agreed system
          </p>
          <p>
            <span className="text-brand-300">›</span> team notified
          </p>
          <p className="text-brand-300">
            › missing information triggers a follow-up
            <span className="ml-0.5 inline-block h-3 w-1.5 translate-y-0.5 bg-brand/80 animate-blink" />
          </p>
          <div className="pt-2">
            <StatusPill status="passed" label="receipt ready" />
          </div>
        </div>
      );
  }
}
