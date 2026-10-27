import { cn } from "@/lib/utils";

export type MascotState = "idle" | "analyzing" | "executing" | "verifying" | "done";

const badge: Record<MascotState, { fill: string; label: string } | null> = {
  idle: null,
  analyzing: { fill: "#08A9F0", label: "Analyzing" },
  executing: { fill: "#6FD3FF", label: "Executing" },
  verifying: { fill: "#FBBF24", label: "Verifying" },
  done: { fill: "#34D399", label: "Done" },
};

/**
 * Placeholder blue dog mascot. Replace with the official Rouvr artwork
 * once available; keep the `state` prop contract.
 */
export function Mascot({
  state = "idle",
  className,
  title = "Rouvr mascot",
  decorative = false,
}: {
  state?: MascotState;
  className?: string;
  title?: string;
  decorative?: boolean;
}) {
  const b = badge[state];
  return (
    <svg
      viewBox="0 0 120 120"
      {...(decorative
        ? { "aria-hidden": true }
        : { role: "img", "aria-label": b ? `${title}: ${b.label}` : title })}
      className={cn("mascot", className)}
      data-state={state}
    >
      <g className="mascot-ear-l">
        <path d="M24 50 L28 14 Q31 8 37 12 L54 34 Z" fill="#0687C2" />
        <path d="M30 40 L32 20 L44 33 Z" fill="#071522" opacity="0.35" />
      </g>
      <g className="mascot-ear-r">
        <path d="M96 50 L92 14 Q89 8 83 12 L66 34 Z" fill="#0687C2" />
        <path d="M90 40 L88 20 L76 33 Z" fill="#071522" opacity="0.35" />
      </g>

      <path
        d="M18 62 Q18 28 60 28 Q102 28 102 62 L102 74 Q102 104 60 106 Q18 104 18 74 Z"
        fill="#08A9F0"
      />
      <path
        d="M18 74 Q18 104 60 106 Q102 104 102 74 L102 70 Q90 96 60 96 Q30 96 18 70 Z"
        fill="#0687C2"
        opacity="0.55"
      />
      <path
        d="M30 40 Q44 32 60 32"
        stroke="#9FE2FF"
        strokeWidth="2"
        strokeLinecap="round"
        fill="none"
        opacity="0.6"
      />

      <ellipse cx="60" cy="84" rx="24" ry="17" fill="#D6F2FF" />

      <g className="mascot-eyes">
        {state === "done" ? (
          <>
            <path d="M38 60 Q44 52 50 60" stroke="#071522" strokeWidth="4" strokeLinecap="round" fill="none" />
            <path d="M70 60 Q76 52 82 60" stroke="#071522" strokeWidth="4" strokeLinecap="round" fill="none" />
          </>
        ) : state === "verifying" ? (
          <>
            <rect x="37" y="56" width="14" height="4" rx="2" fill="#071522" />
            <rect x="69" y="56" width="14" height="4" rx="2" fill="#071522" />
          </>
        ) : state === "executing" ? (
          <>
            <rect x="34" y="51" width="52" height="13" rx="6.5" fill="#071522" />
            <rect className="mascot-visor" x="38" y="55" width="12" height="5" rx="2.5" fill="#6FD3FF" />
          </>
        ) : (
          <g className={state === "analyzing" ? "mascot-look" : "mascot-blink"}>
            <circle cx="44" cy="58" r="5" fill="#071522" />
            <circle cx="76" cy="58" r="5" fill="#071522" />
            <circle cx="45.5" cy="56.5" r="1.6" fill="#fff" />
            <circle cx="77.5" cy="56.5" r="1.6" fill="#fff" />
          </g>
        )}
      </g>

      <ellipse cx="60" cy="76" rx="7" ry="4.5" fill="#071522" />
      <path
        d="M60 80 Q60 88 52 88 M60 80 Q60 88 68 88"
        stroke="#071522"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
      />

      {b && (
        <g>
          <circle cx="100" cy="100" r="11" fill="#071522" />
          <circle cx="100" cy="100" r="7" fill={b.fill} className="mascot-badge" />
          {state === "done" && (
            <path d="M96.5 100 L99 102.5 L103.5 97.5" stroke="#071522" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          )}
        </g>
      )}
    </svg>
  );
}
