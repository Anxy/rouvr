import { cn } from "@/lib/utils";
import type { PipelineFlag, PipelineState } from "@/lib/api/workspace";

// PipelineStrip: Contract · Knowledge · Build · Review · Evidence, each
// rendered ✓ / ✗ / – straight from the ledger `pipeline` object on the
// task row. That object is computed server-side ONLY from real task state
// (see the mapping comment above _pipeline_flags in ledger/app.py):
//   contract  — a contracts row exists (acceptance criteria live here);
//   knowledge — plan_slug/category linkage in a contract body or a
//               chat:create / plan:convert receipt (intake plan linkage);
//   build     — worker output exists (evidence kind step/tests/test_output/
//               criteria, or a results row); ✗ when the latest result verdict
//               is negative (blocks the approve gate);
//   review    — reviewer-kind evidence (reviewer/review/review_note) with a
//               non-negative latest verdict, or state done (approval requires
//               a passing verify); ✗ on a negative reviewer verdict;
//   evidence  — >= 1 evidence row exists (the approve gate counts rows).
// The strip never fetches per-task detail: the board mini-strip renders from
// the row's `pipeline` field, and the detail page reuses the same object.

export const PIPELINE_STEPS = [
  { key: "contract", label: "Contract" },
  { key: "knowledge", label: "Knowledge" },
  { key: "build", label: "Build" },
  { key: "review", label: "Review" },
  { key: "evidence", label: "Evidence" },
] as const;

export type PipelineStepKey = (typeof PIPELINE_STEPS)[number]["key"];

const GLYPH: Record<PipelineFlag, string> = { done: "✓", attention: "✗", pending: "–" };

const TONE: Record<PipelineFlag, string> = {
  done: "text-success",
  attention: "text-danger",
  pending: "text-muted/60",
};

function glyph(flag: PipelineFlag | undefined): { mark: string; tone: string } {
  const f: PipelineFlag = flag === "done" || flag === "attention" ? flag : "pending";
  return { mark: GLYPH[f], tone: TONE[f] };
}

export function PipelineStrip({
  pipeline,
  variant = "full",
  className,
}: {
  pipeline?: PipelineState | null;
  variant?: "full" | "mini";
  className?: string;
}) {
  if (!pipeline) return null;
  if (variant === "mini") {
    return (
      <span
        data-pipeline-strip="mini"
        className={cn("inline-flex items-center gap-2 font-mono text-[10px]", className)}
        aria-label="pipeline status"
      >
        {PIPELINE_STEPS.map((step) => {
          const { mark, tone } = glyph(pipeline[step.key]);
          return (
            <span key={step.key} title={`${step.label}: ${pipeline[step.key] ?? "pending"}`} className={tone}>
              {step.label.slice(0, 1)}
              {mark}
            </span>
          );
        })}
      </span>
    );
  }
  return (
    <ol
      data-pipeline-strip="full"
      className={cn("flex flex-wrap items-center gap-x-4 gap-y-1", className)}
      aria-label="pipeline status"
    >
      {PIPELINE_STEPS.map((step) => {
        const { mark, tone } = glyph(pipeline[step.key]);
        return (
          <li
            key={step.key}
            className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider"
          >
            <span className={tone} aria-hidden>
              {mark}
            </span>
            <span className="text-muted">{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
