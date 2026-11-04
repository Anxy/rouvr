// Task-detail helpers (P3): pure functions over the real ledger rows on
// TaskDetail. No fetches, no invented text — every string returned comes
// from the task's contracts / evidence / receipts, or from the explicit
// fence-code table below. Unknown text passes through untouched.

export interface ContractRow {
  body: string;
}

export interface EvidenceRow {
  id: number;
  kind: string;
  body: string;
  created_at: string;
}

export interface ReceiptRow {
  action: string;
  body: string;
}

// Fence codes the ledger actually emits (escalation/reviewer evidence
// `fence` fields, worker error names) mapped to plain English. The table
// is intentionally small: anything not listed here falls back to a
// generic humanization when it is code-shaped, and to the raw text when
// it is not a code at all.
export const ATTENTION_CODE_ENGLISH: Record<string, string> = {
  E_DOWNSTREAM: "Downstream step failed",
  E_CONTRACT: "Contract problem",
  E_MOVED: "Task moved",
  rework_exhausted: "Rework exhausted",
  approval_wait: "Waiting for approval",
  FileNotFoundError: "File not found",
};

// Evidence kinds the ledger reads for the list-row attention reason
// (see _ATTENTION_KINDS in ledger/app.py). The detail endpoint does NOT
// carry `attention`, so the page derives it from these rows the same way.
export const ATTENTION_EVIDENCE_KINDS = new Set([
  "escalation",
  "reviewer",
  "review",
  "review_note",
]);

function genericHumanize(code: string): string {
  let words = code;
  if (/^E_/.test(words)) words = words.slice(2);
  words = words
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .trim();
  if (!words) return code;
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function splitAttempt(text: string): { base: string; attempt: string | null } {
  const m = text.match(/\s*\(attempt (\d+)\)\s*$/);
  if (!m) return { base: text.trim(), attempt: null };
  return { base: text.slice(0, m.index).trim(), attempt: m[1] };
}

// Turn one attention/reason string into `plain words (CODE)`.
// Returns null only for blank input; unknown (non-code) text renders
// as-is with no code appended.
export function describeBlockedReason(reason: string | null | undefined): {
  plain: string;
  code: string | null;
  display: string;
} | null {
  if (!reason || !reason.trim()) return null;
  const { base, attempt } = splitAttempt(reason);
  if (!base) return null;
  const tail = attempt ? `, attempt ${attempt}` : "";

  const eMatch = base.match(/\bE(?:_[A-Z0-9]+|(?: [A-Z0-9]+)+)\b/);
  const snakeMatch = base.match(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/);
  const errorMatch = base.match(
    /\b[A-Za-z0-9]*(?:Error|Exception|Timeout|Failure)\b/,
  );
  const token = eMatch
    ? eMatch[0].replace(/ /g, "_").toUpperCase()
    : (snakeMatch?.[0] ?? errorMatch?.[0] ?? null);
  if (token) {
    const plain = ATTENTION_CODE_ENGLISH[token] ?? genericHumanize(token);
    return { plain, code: token, display: `${plain} (${token}${tail})` };
  }

  // The ledger humanizes fences before serving them (`_` -> ` `), so
  // "rework_exhausted" arrives as "rework exhausted" and "E_MOVED" as
  // "E MOVED". Reverse the normalization for table members only.
  const up = base.replace(/\s+/g, "_").toUpperCase();
  const lo = base.replace(/\s+/g, "_").toLowerCase();
  const known = ATTENTION_CODE_ENGLISH[up] ? up : ATTENTION_CODE_ENGLISH[lo] ? lo : null;
  if (known) {
    return {
      plain: ATTENTION_CODE_ENGLISH[known],
      code: known,
      display: `${ATTENTION_CODE_ENGLISH[known]} (${known}${tail})`,
    };
  }
  return { plain: base, code: null, display: attempt ? `${base} (attempt ${attempt})` : base };
}

function fenceParts(fence: unknown): { code: string | null; attempt: string | null } {
  if (typeof fence !== "string" || !fence.trim()) return { code: null, attempt: null };
  const parts = fence.trim().split(":");
  const code = parts[parts.length - 1].replace(/_/g, " ").trim() || null;
  let attempt: string | null = null;
  for (const part of parts) {
    const m = part.trim().match(/^attempt-(\d+)$/);
    if (m) {
      attempt = m[1];
      break;
    }
  }
  return { code, attempt };
}

// Mirror of the ledger's _attention_reason (ledger/app.py): latest
// escalation/reviewer evidence body -> one short string. An explicit
// ledger `attention` string (list rows carry one) wins when present.
export function attentionFromEvidence(
  evidence: EvidenceRow[],
  attention?: string | null,
): string | null {
  if (attention && attention.trim()) return attention.trim();
  let raw: string | null = null;
  for (const e of evidence) {
    if (ATTENTION_EVIDENCE_KINDS.has(e.kind)) raw = e.body;
  }
  if (raw === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = raw;
  }
  let text: string | null = null;
  let attempt: string | null = null;
  if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
    const record = parsed as Record<string, unknown>;
    const payload =
      record["payload"] !== null && typeof record["payload"] === "object"
        ? (record["payload"] as Record<string, unknown>)
        : null;
    for (const key of ["reason", "note", "message", "text", "verdict"]) {
      const val = payload?.[key];
      if (typeof val === "string" && val.trim()) {
        text = val.trim();
        break;
      }
    }
    if (text === null) {
      for (const key of ["reason", "note", "message", "text", "verdict"]) {
        const val = record[key];
        if (typeof val === "string" && val.trim()) {
          text = val.trim();
          break;
        }
      }
    }
    const fence = fenceParts(record["fence"]);
    attempt = fence.attempt;
    if (text === null) text = fence.code;
  } else if (typeof parsed === "string") {
    text = parsed.trim();
  } else {
    text = JSON.stringify(parsed).trim();
  }
  if (!text) return null;
  text = text.replace(/_/g, " ");
  if (attempt && !text.includes(attempt)) text = `${text} (attempt ${attempt})`;
  return text.length > 200 ? text.slice(0, 200) : text;
}

// One line on why this task exists: the first acceptance criterion, else
// the intent's first line. "no contract recorded" only when no contract
// row exists (or nothing parseable plus a blank intent).
export function whyTaskExists(task: {
  contracts: ContractRow[];
  intent: string;
}): string {
  for (const c of task.contracts) {
    try {
      const parsed: unknown = JSON.parse(c.body);
      if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
        const criteria = (parsed as Record<string, unknown>)["acceptance_criteria"];
        if (Array.isArray(criteria)) {
          for (const item of criteria) {
            if (typeof item === "string" && item.trim()) return item.trim();
          }
        }
      }
    } catch {
      /* unparseable contract body: fall through to intent */
    }
  }
  const firstLine = (task.intent ?? "").split("\n")[0]?.trim() ?? "";
  if (firstLine) return firstLine;
  return "no contract recorded";
}

export interface KnowledgeRefs {
  planSlugs: string[];
  categories: string[];
  citations: string[];
}

const CITATION_RE = /knowledge:\d+@v\d+/g;

// Promote intake linkage out of the contract JSON (plus the receipt
// bodies the ledger treats as the same linkage): plan_slug values,
// category values, and cited knowledge slugs. Order-stable, deduplicated.
export function collectKnowledgeRefs(
  contracts: ContractRow[],
  receipts: ReceiptRow[],
): KnowledgeRefs {
  const planSlugs: string[] = [];
  const categories: string[] = [];
  const citations: string[] = [];
  const seen = new Set<string>();
  const bodies: string[] = [...contracts.map((c) => c.body)];
  for (const r of receipts) {
    if (r.action === "chat:create" || r.action === "plan:convert") bodies.push(r.body);
  }
  for (const body of bodies) {
    try {
      const parsed: unknown = JSON.parse(body);
      if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
        const record = parsed as Record<string, unknown>;
        const slug = record["plan_slug"];
        if (typeof slug === "string" && slug.trim() && !seen.has(`plan:${slug}`)) {
          seen.add(`plan:${slug}`);
          planSlugs.push(slug.trim());
        }
        const category = record["category"];
        if (typeof category === "string" && category.trim() && !seen.has(`cat:${category}`)) {
          seen.add(`cat:${category}`);
          categories.push(category.trim());
        }
      }
    } catch {
      /* raw bodies still get citation-scanned below */
    }
    for (const m of body.matchAll(CITATION_RE)) {
      if (!seen.has(m[0])) {
        seen.add(m[0]);
        citations.push(m[0]);
      }
    }
  }
  return { planSlugs, categories, citations };
}

// One readable label per evidence row from its kind/body. Raw JSON stays
// behind a collapsed <details> in the UI; this is the timeline line.
export function evidenceLabel(kind: string, body: string): string {
  void kind;
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      let label: string | null = null;
      for (const key of ["label", "title", "reason", "message", "text", "note", "summary"]) {
        const val = record[key];
        if (typeof val === "string" && val.trim()) {
          label = val.trim();
          break;
        }
      }
      const payload =
        record["payload"] !== null && typeof record["payload"] === "object"
          ? (record["payload"] as Record<string, unknown>)
          : null;
      const verdict =
        (typeof record["verdict"] === "string" && record["verdict"].trim()) ||
        (payload && typeof payload["verdict"] === "string" && payload["verdict"].trim()) ||
        null;
      if (label && verdict) return `${label} (verdict: ${String(verdict).trim()})`;
      if (label) return label;
      if (verdict) return `verdict: ${String(verdict).trim()}`;
    } else if (typeof parsed === "string" && parsed.trim()) {
      return parsed.trim().slice(0, 160);
    }
  } catch {
    /* not JSON: fall through to the raw slice */
  }
  return body.slice(0, 160);
}

export function formatEvidenceTime(iso: string): string {
  if (!iso) return "unknown";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleString();
}
