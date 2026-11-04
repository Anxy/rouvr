// Knowledge helpers shared by the workspace knowledge page and its guard
// tests. Everything here derives from data already on hand: versions parse
// out of the ledger citation string (knowledge:{id}@v{n}), counts tally the
// loaded shortlist. Nothing here invents totals.

export const DEFAULT_QUERY = "the";

export const KIND_OPTIONS = ["rule", "decision", "note", "snapshot", "skill"] as const;

export type KnowledgeKind = (typeof KIND_OPTIONS)[number];

export function citationId(citation: string): string {
  const m = citation.match(/^knowledge:(\d+)@v\d+$/);
  return m ? m[1] : citation;
}

// Version shown per entry comes from the citation the ledger minted
// (shortlist rows carry no separate version field).
export function citationVersion(citation: string): string | null {
  const m = citation.match(/@v(\d+)$/);
  return m ? `v${m[1]}` : null;
}

export function isVerified(hit: { verified: number | boolean }): boolean {
  return hit.verified === 1 || hit.verified === true;
}

// Review workflow: the ledger tracks review_state per entry (pending by
// default; verified | rejected | needs_revision after review). List badges
// render only for rejected/needs_revision rows — verified rows already
// carry the verified pill, pending rows carry neither.
export function reviewStateLabel(state?: string | null): string {
  switch (state) {
    case "verified":
      return "Verified";
    case "rejected":
      return "Rejected";
    case "needs_revision":
      return "Needs revision";
    default:
      return "Pending";
  }
}

export function isPinned(hit: { pinned?: number | boolean }): boolean {
  return hit.pinned === 1 || hit.pinned === true;
}

// Count the loaded shortlist by kind. Callers label the result
// "showing N" so it never reads as a ledger-wide total.
export function countKinds(hits: { kind: string }[]): Record<KnowledgeKind, number> {
  const counts = Object.fromEntries(KIND_OPTIONS.map((k) => [k, 0])) as Record<
    KnowledgeKind,
    number
  >;
  for (const h of hits) {
    if ((KIND_OPTIONS as readonly string[]).includes(h.kind)) {
      counts[h.kind as KnowledgeKind] += 1;
    }
  }
  return counts;
}
