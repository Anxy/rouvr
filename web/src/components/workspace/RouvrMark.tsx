// RouvrMark brand asset, ported verbatim from the Figma source
// (design/figma-export/ROUVR-AI-Engineering-Workspace/src/App.tsx).
// The fills are the mascot's fixed brand colours, not theme UI colours,
// so they stay as-ported; everything around the mark uses theme tokens.

export function RouvrMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      aria-label="Rouvr blue dog mascot"
    >
      <rect width="40" height="40" rx="12" fill="#08A9F0" />
      <path
        d="M10 13.5 14.5 8l3 5h5l3-5 4.5 5.5-2 4.2v7.1c0 4-3.6 7.2-8 7.2s-8-3.2-8-7.2v-7.1z"
        fill="#061522"
      />
      <path
        d="M15.5 20.5c1.3-1.2 7.7-1.2 9 0v4c-1 2.4-2.5 3.5-4.5 3.5s-3.5-1.1-4.5-3.5z"
        fill="#EAF9FF"
      />
      <circle cx="16.5" cy="18" r="1.2" fill="#EAF9FF" />
      <circle cx="23.5" cy="18" r="1.2" fill="#EAF9FF" />
      <path
        d="m18 23 2 1.4 2-1.4"
        stroke="#061522"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
