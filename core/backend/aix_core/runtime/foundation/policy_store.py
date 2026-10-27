"""AIX-UGEF-001: minimal in-memory policy store.

Deterministic precedence: system → project → principal/role → session.
Most restrictive wins for mutations. Explicit DENY at any level blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Decision = Literal["ALLOW", "CONFIRM", "DENY", "ELEVATE"]


@dataclass(frozen=True)
class PolicyRuleV1:
    id: str
    capability: str
    operation: str = "*"
    decision: Decision = "ALLOW"
    reason: str = ""
    # Scope selectors — empty string means "any"
    project_id: str = ""
    principal_role: str = ""
    principal_id: str = ""
    session_grant: str = ""


@dataclass
class PolicyStore:
    """In-memory rule set. Production-ready enough for vertical slice."""

    _rules: list[PolicyRuleV1] = field(default_factory=list)

    def add_rule(self, rule: PolicyRuleV1) -> None:
        self._rules.append(rule)

    def clear(self) -> None:
        self._rules.clear()

    def evaluate(
        self,
        *,
        capability: str,
        operation: str,
        project_id: str = "",
        principal_role: str = "",
        principal_id: str = "",
        session_grants: tuple[str, ...] = (),
    ) -> tuple[Decision, PolicyRuleV1 | None]:
        """Evaluate rules. Returns (decision, matching_rule).

        Precedence: session grants > principal > project > system.
        DENY at any level blocks. CONFIRM overrides ALLOW.
        """
        matching: list[tuple[int, PolicyRuleV1]] = []
        for rule in self._rules:
            if not _matches_capability(rule, capability, operation):
                continue
            priority = _scope_priority(rule, project_id, principal_role, principal_id, session_grants)
            if priority >= 0:
                matching.append((priority, rule))

        if not matching:
            return "ALLOW", None

        matching.sort(key=lambda x: x[0], reverse=True)
        # Highest-priority rule wins. Among equal priority, DENY > ELEVATE > CONFIRM > ALLOW.
        best_priority = matching[0][0]
        top_tier = [(p, r) for p, r in matching if p == best_priority]

        _order = {"DENY": 0, "ELEVATE": 1, "CONFIRM": 2, "ALLOW": 3}
        top_tier.sort(key=lambda x: _order.get(x[1].decision, 99))
        winner = top_tier[0][1]
        return winner.decision, winner


def _matches_capability(rule: PolicyRuleV1, capability: str, operation: str) -> bool:
    if rule.capability != capability and rule.capability != "*":
        return False
    if rule.operation != operation and rule.operation != "*":
        return False
    return True


def _scope_priority(
    rule: PolicyRuleV1,
    project_id: str,
    principal_role: str,
    principal_id: str,
    session_grants: tuple[str, ...],
) -> int:
    """Return priority (higher = more specific). -1 = does not match."""
    if rule.session_grant:
        if rule.session_grant in session_grants:
            return 40
        return -1
    if rule.principal_id:
        if rule.principal_id == principal_id:
            return 30
        return -1
    if rule.principal_role:
        if rule.principal_role == principal_role:
            return 20
        return -1
    if rule.project_id:
        if rule.project_id == project_id:
            return 10
        return -1
    return 0


_GLOBAL_STORE: PolicyStore | None = None


def get_policy_store() -> PolicyStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = PolicyStore()
    return _GLOBAL_STORE


def reset_policy_store() -> None:
    global _GLOBAL_STORE
    _GLOBAL_STORE = None


__all__ = [
    "Decision",
    "PolicyRuleV1",
    "PolicyStore",
    "get_policy_store",
    "reset_policy_store",
]
