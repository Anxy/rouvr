"use client";

import { useEffect, useRef, useState } from "react";
import { m } from "motion/react";
import { Check, RotateCw } from "lucide-react";

type Visual = "graph" | "progress" | "agents" | "tests" | "recovery" | "approval";

const inView = { once: true, margin: "0px 0px -10% 0px" } as const;

export function CapabilityVisual({ type }: { type: Visual }) {
  switch (type) {
    case "graph":
      return <GraphVisual />;
    case "progress":
      return <ProgressVisual />;
    case "agents":
      return <AgentsVisual />;
    case "tests":
      return <TestsVisual />;
    case "recovery":
      return <RecoveryVisual />;
    case "approval":
      return <ApprovalVisual />;
  }
}

function GraphVisual() {
  const facets = ["Role", "Ambitions", "Decisions", "Way of working", "Feedback"];
  const actions = ["Inspect", "Revise", "Stays"];
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<string[]>([]);

  useEffect(() => {
    const root = rootRef.current;
    const list = listRef.current;
    const panel = panelRef.current;
    if (!root || !list || !panel) return;

    const measure = () => {
      const rb = root.getBoundingClientRect();
      const lb = list.getBoundingClientRect();
      const pb = panel.getBoundingClientRect();
      if (pb.left <= lb.right + 12) {
        setPaths([]);
        return;
      }
      const x2 = pb.left - rb.left;
      const y2 = pb.top - rb.top + pb.height / 2;
      const next = [...list.children].map((item) => {
        const ib = item.getBoundingClientRect();
        const x1 = ib.right - rb.left;
        const y1 = ib.top - rb.top + ib.height / 2;
        const bend = (x2 - x1) * 0.55;
        return `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${(x1 + bend).toFixed(1)} ${y1.toFixed(1)}, ${(x2 - bend).toFixed(1)} ${y2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
      });
      setPaths((current) =>
        current.length === next.length && current.every((path, i) => path === next[i]) ? current : next,
      );
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={rootRef} className="@container relative w-full" aria-hidden>
      <svg className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
        <defs>
          <filter id="capability-string-glow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="2.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {paths.map((d, i) => (
          <g key={i}>
            <path d={d} fill="none" stroke="rgb(8 169 240 / 0.35)" strokeWidth="1.25" />
            <path
              d={d}
              fill="none"
              pathLength={100}
              stroke="#9fe2ff"
              strokeWidth="2.25"
              strokeLinecap="round"
              filter="url(#capability-string-glow)"
              className="string-light"
              style={{ animationDelay: `${i * 0.32}s` }}
            />
          </g>
        ))}
      </svg>
      <div className="relative grid w-full items-stretch gap-3 @min-[36rem]:grid-cols-[minmax(0,1fr)_5.5rem_minmax(11rem,0.82fr)] @min-[36rem]:gap-0">
        <ul ref={listRef} className="grid grid-cols-1 gap-2">
          {facets.map((label, i) => (
            <m.li
              key={label}
              className="flex items-center gap-2.5 rounded-xl border border-steel/50 bg-abyss/60 px-3 py-2"
              initial={{ opacity: 0, y: 6 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={inView}
              transition={{ delay: 0.08 + i * 0.05, duration: 0.35 }}
            >
              <span className="size-1.5 shrink-0 rounded-full bg-brand" />
              <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-cloud">{label}</span>
            </m.li>
          ))}
        </ul>
        <div className="hidden @min-[36rem]:block" />
        <div
          ref={panelRef}
          className="relative flex flex-col justify-center rounded-2xl border border-brand/35 bg-brand/[0.08] px-4 py-4"
        >
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-brand-300">Shared understanding</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {actions.map((action) => (
              <span
                key={action}
                className="rounded-full border border-brand/30 bg-abyss px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-brand-300"
              >
                {action}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ProgressVisual() {
  const rows = [
    { l: "Needs your judgement", v: 1 },
    { l: "In progress", v: 0.72 },
    { l: "Ready for review", v: 0.34 },
  ];
  return (
    <div className="space-y-3" aria-hidden>
      {rows.map((r, i) => (
        <div key={r.l}>
          <p className="font-mono text-[11px] text-muted">{r.l}</p>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-steel/40">
            <m.div
              className="h-full origin-left rounded-full bg-gradient-to-r from-brand to-brand-300"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: r.v }}
              viewport={inView}
              transition={{ duration: 1.2, delay: 0.2 + i * 0.15, ease: [0.22, 1, 0.36, 1] }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function AgentsVisual() {
  const agents = ["research", "create", "build"];
  return (
    <div className="relative grid h-28 place-items-center" aria-hidden>
      <svg viewBox="0 0 96 96" className="absolute size-24 overflow-visible">
        <defs>
          <filter id="capability-orbit-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="1.6" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <circle
          cx="48"
          cy="48"
          r="46"
          fill="none"
          stroke="#24445e"
          strokeWidth="1.25"
          strokeDasharray="3 5"
          className="origin-center animate-spin-slow"
          style={{ transformBox: "fill-box" }}
        />
        {[0, 1.1].map((delay) => (
          <circle
            key={delay}
            cx="48"
            cy="48"
            r="46"
            fill="none"
            pathLength={100}
            stroke="#9fe2ff"
            strokeWidth="2.25"
            strokeLinecap="round"
            filter="url(#capability-orbit-glow)"
            className="string-light"
            style={{ animationDelay: `${delay}s` }}
          />
        ))}
      </svg>
      <div className="relative z-10 rounded-lg border border-brand/50 bg-brand/10 px-3 py-1.5 font-mono text-[11px] text-brand-300">
        pack
      </div>
      {agents.map((a, i) => (
        <span
          key={a}
          className="absolute rounded-md border border-steel bg-abyss px-2 py-0.5 font-mono text-[10px] text-muted"
          style={{
            transform: `rotate(${i * 120 - 90}deg) translateX(${i === 0 ? 54 : 76}px) rotate(${-(i * 120 - 90)}deg)`,
          }}
        >
          {a}
        </span>
      ))}
    </div>
  );
}

function TestsVisual() {
  const tests = ["figures match the source", "materials match the brief", "changes reviewed again"];
  return (
    <ul className="space-y-2 font-mono text-[11px]" aria-hidden>
      {tests.map((t, i) => (
        <m.li
          key={t}
          className="flex items-center gap-2 rounded-md border border-steel/50 bg-abyss/60 px-2.5 py-1.5 text-muted"
          initial={{ opacity: 0, x: -8 }}
          whileInView={{ opacity: 1, x: 0 }}
          viewport={inView}
          transition={{ delay: 0.2 + i * 0.15 }}
        >
          <Check className="size-3.5 text-success" />
          <span className="truncate">{t}</span>
        </m.li>
      ))}
    </ul>
  );
}

function RecoveryVisual() {
  return (
    <div className="flex flex-wrap items-center gap-3 font-mono text-[11px]" aria-hidden>
      <span className="rounded-md border border-danger/40 bg-danger/10 px-2 py-1 text-danger">interrupted</span>
      <RotateCw className="size-4 text-brand" />
      <span className="rounded-md border border-brand/40 bg-brand/10 px-2 py-1 text-brand-300">
        valid work kept
      </span>
    </div>
  );
}

function ApprovalVisual() {
  return (
    <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]" aria-hidden>
      <span className="rounded-md border border-steel bg-abyss px-2 py-1 text-muted">record updated</span>
      <span className="rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-warning">
        campaign sent
      </span>
      <span className="rounded-md bg-brand px-2.5 py-1 text-abyss">receipt checked</span>
    </div>
  );
}
