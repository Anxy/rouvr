"""Wave 5: AgentShield admission rules for CI (30 rules, not 817).

Each rule names one precise authority/durability violation shape observed in
the AIX codebase (Waves 0-4). `scan_text`/`scan_diff` flag matches for human
or CI review. Rules are data: adding coverage means appending one entry.
Severity: P0 = product fire (wrong target), P1 = ops fire (stuck/diverged).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ShieldRule:
    rule_id: str
    pattern: str
    severity: str  # P0 | P1
    rationale: str


RULES: tuple[ShieldRule, ...] = (
    # --- Inference becoming authority (P0) ---
    ShieldRule("SH-INF-01", r"def _infer_target_files", "P0", "file inference function present; must be quarantined behind posture flag"),
    ShieldRule("SH-INF-02", r"_infer_python_target_symbol", "P0", "heuristic symbol inference; missing must stay missing"),
    ShieldRule("SH-INF-03", r'=\s*"codeintel_first"', "P0", "CodeIntel-first default or hardcoded policy; default must be direct/off"),
    ShieldRule("SH-INF-04", r"or str\(entity\.get\(", "P0", "entity-derived fallback for a missing contract field"),
    ShieldRule("SH-INF-05", r"target_\w+ or .*anchor", "P0", "anchor supplying a missing contract target"),
    ShieldRule("SH-INF-06", r"single.*entit.*(target|anchor)|entit.*\[0\].*(target|file)", "P0", "lone-entity-becomes-target pattern"),
    ShieldRule("SH-INF-07", r"fallback.*polic.*return|return.*fallback", "P0", "fallback result returned as usable authority"),
    ShieldRule("SH-INF-08", r"confidence.{0,20}0\.35|placeholder.*citation", "P0", "synthetic low-confidence evidence fabricated"),
    # --- Scope expansion (P0) ---
    ShieldRule("SH-SCP-01", r"adjacent_files", "P0", "dependency-graph files near the target set; advisory only, never scope"),
    ShieldRule("SH-SCP-02", r"extra_candidates|scope.*union|union.*scope", "P0", "retrieval candidates merged into the authorized set"),
    ShieldRule("SH-SCP-03", r"rglob|walk.*workspace.*target", "P0", "workspace-wide search feeding target selection (candidates only)"),
    ShieldRule("SH-SCP-04", r"changed_targets\s*=", "P0", "execution file targets assigned; must trace to contract/amendment only"),
    ShieldRule("SH-SCP-05", r"selected|single_source_of_truth.*infer", "P0", "a selected/inferred file presented as decided"),
    ShieldRule("SH-SCP-06", r"transitive|depth\s*>\s*1|recursive.*depend", "P0", "transitive dependency walk expanding the working set"),
    # --- Provenance & freshness (P0) ---
    ShieldRule("SH-PRV-01", r'"unknown"', "P0", "unknown snapshot/commit treated as a value instead of a classified gap"),
    ShieldRule("SH-PRV-02", r"snapshot_id.*or \"\"|commit_sha.*or \"\"", "P0", "empty-string provenance defaulting (must be explicit unknown)"),
    ShieldRule("SH-PRV-03", r"freshness.*current", "P0", "freshness=current asserted; allowed only on HEAD match"),
    ShieldRule("SH-PRV-04", r"confidence(?!.*(0 <=|0\.0 <=|0<=|Field\(.*ge=0))", "P1", "confidence used without bounds check"),
    ShieldRule("SH-PRV-05", r"except.*(JSONDecodeError|ValueError).*(continue|pass)", "P1", "malformed provenance silently skipped (must be classified)"),
    # --- Durability honesty (P1) ---
    ShieldRule("SH-DUR-01", r"heartbeat_timeout_seconds\s*=\s*\d+", "P1", "numeric heartbeat on a backend-shelling activity (Option B: None)"),
    ShieldRule("SH-DUR-02", r"except Exception:\s*\n\s*pass", "P1", "swallowed exception hiding cancellation/failure"),
    ShieldRule("SH-DUR-03", r"maximum_attempts\s*=\s*[2-9]", "P1", "retries on a possibly-mutating step without idempotency key"),
    ShieldRule("SH-DUR-04", r"allow_inprocess|fallback.*json|json.*fallback", "P1", "silent file fallback when PG configured (must fail closed)"),
    ShieldRule("SH-DUR-05", r"CREATE TABLE(?! IF NOT EXISTS)", "P1", "non-idempotent DDL outside the single DDL path"),
    ShieldRule("SH-DUR-06", r"phase\s*==\s*\"RUNNING_STEP\"", "P1", "stringly-typed phase check; new stuck-detectors forbidden"),
    # --- Amendment & approval (P0) ---
    ShieldRule("SH-AMD-01", r"lane_approval_id.*=\s*\"\"", "P0", "empty lane approval default (must be required)"),
    ShieldRule("SH-AMD-02", r"self.?approv|approv.*self|router.*amend", "P0", "self-approval or router-driven scope growth"),
    ShieldRule("SH-AMD-03", r"\.amendments\s*=\s*\[|amendments\.append", "P0", "amendment log mutated in place (must be append-only new object)"),
    ShieldRule("SH-AMD-04", r"AIX_CODEINTEL.*on|posture.*==.*on", "P0", "\"on\" posture path reachable (illegal in v2 MVP)"),
    ShieldRule("SH-AMD-05", r"advisory_degraded\s*=\s*False", "P1", "degraded flag forced False (must be computed, never asserted)"),
    ShieldRule("SH-AMD-06", r"get_context\(.*retrieval_policy=\{\s*\}", "P1", "retrieval call without explicit policy (must declare mode)"),
)


@dataclass(frozen=True)
class ShieldFinding:
    rule_id: str
    severity: str
    line_no: int
    line: str
    rationale: str


def scan_text(source: str) -> list[ShieldFinding]:
    """Scan source text; return one finding per rule hit (first hit per rule per line)."""
    findings: list[ShieldFinding] = []
    for lineno, line in enumerate(str(source or "").splitlines(), start=1):
        for rule in RULES:
            try:
                if re.search(rule.pattern, line):
                    findings.append(
                        ShieldFinding(
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            line_no=lineno,
                            line=line.strip()[:200],
                            rationale=rule.rationale,
                        )
                    )
            except re.error:
                continue
    return findings


def scan_diff(diff_text: str) -> list[ShieldFinding]:
    """Scan only added lines of a unified diff (lines starting with +, not +++)."""
    added = "\n".join(
        line[1:]
        for line in str(diff_text or "").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return scan_text(added)


def rule_ids() -> list[str]:
    return [rule.rule_id for rule in RULES]
