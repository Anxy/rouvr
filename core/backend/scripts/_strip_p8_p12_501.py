"""One-shot: remove P8-P12 HTTP 501 hasattr gates from execution_kernel_router."""
from __future__ import annotations

import re
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "routers" / "execution_kernel_router.py"
text = path.read_text(encoding="utf-8")

methods = [
    "observe_learning",
    "list_learning",
    "advance_learning",
    "classify_learning_scope",
    "set_learning_confidence",
    "approve_learning",
    "reject_learning",
    "semantic_technology_decision",
    "semantic_search",
    "decompose_planning_objective",
    "evaluate_planning_autonomy",
    "detect_planning_replan_triggers",
    "propose_plan_mutation",
    "reasoning_golden_scenarios",
    "reason_organizationally",
    "enterprise_scenarios",
    "portfolio_intelligence_view",
    "start_enterprise_improvement_cycle",
    "strategic_programme_resume",
    "list_enterprise_audit",
]

pattern = re.compile(
    r"\n    if not hasattr\(service, \"(?P<method>[^\"]+)\"\):\n"
    r"        raise HTTPException\(\n"
    r"            status_code=status\.HTTP_501_NOT_IMPLEMENTED,\n"
    r"            detail=\{[^}]+\},\n"
    r"        \)\n"
)

removed = 0


def _repl(match: re.Match[str]) -> str:
    global removed
    if match.group("method") in methods:
        removed += 1
        return "\n"
    return match.group(0)


new_text = pattern.sub(_repl, text)
path.write_text(new_text, encoding="utf-8")
print(f"removed={removed} remaining_501={new_text.count('HTTP_501_NOT_IMPLEMENTED')}")
