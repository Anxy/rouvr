"""OutcomeContract proposer — intelligence proposes; control plane seals.

Flow:
  WorkHub/objective text
    → propose (LLM and/or heuristic)
    → validate_outcome_contract_structure
    → seal_outcome_contract
    → OutcomeContractProposal (accepted | rejected)

The model never seals. Invalid proposals are rejected, not repaired into truth.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.objective.outcome_contract import (
    ApiFacetV1,
    ApiOperationSpecV1,
    AuthFacetV1,
    DbFacetV1,
    IntegrationFacetV1,
    IntegrationMappingV1,
    OutcomeContractV1,
    OutcomeFacetsV1,
    UiFacetV1,
    seal_outcome_contract,
    validate_outcome_contract_structure,
)
from backend.aix_core.runtime.objective.product_class import is_product_class_objective

ProposalSource = Literal["llm", "heuristic", "rejected"]

_DOMAIN_NOUNS = (
    "student",
    "students",
    "attendance",
    "customer",
    "customers",
    "supplier",
    "suppliers",
    "product",
    "products",
    "warehouse",
    "warehouses",
    "inventory",
    "invoice",
    "invoices",
    "payment",
    "payments",
    "order",
    "orders",
    "user",
    "users",
    "role",
    "roles",
    "notification",
    "notifications",
    "report",
    "reports",
    "audit",
    "expense",
    "expenses",
    "budget",
    "budgets",
    "category",
    "categories",
)

OUTCOME_CONTRACT_PROPOSER_SYSTEM_PROMPT = (
    "You propose an OutcomeContractV1 for a governed AIX objective. "
    "Output ONLY valid JSON (no markdown) with this shape:\n"
    "{\n"
    '  "definition_of_done": ["..."],\n'
    '  "ui_operations": ["register_student", ...],\n'
    '  "ui_views": ["..."],\n'
    '  "api_operations": [{"id": "students.create", "path_or_action": "POST /students"}, ...],\n'
    '  "db_entities": ["students", ...],\n'
    '  "auth_actors": ["staff"],\n'
    '  "auth_permissions": ["attendance.write"],\n'
    '  "mappings": [\n'
    '    {"from_facet": "UI", "from_id": "register_student", "to_facet": "API", "to_id": "students.create"},\n'
    '    {"from_facet": "API", "from_id": "students.create", "to_facet": "DB", "to_id": "students"}\n'
    "  ]\n"
    "}\n"
    "Rules: outcome-centric (operations/entities), not filenames. "
    "Every UI op must map to an API op; every API op must map to a DB entity. "
    "Do not invent production deploy or WorkHub lifecycle effects."
)


@dataclass(frozen=True)
class OutcomeContractProposal:
    ok: bool
    code: str
    detail: str
    contract: OutcomeContractV1 | None = None
    source: ProposalSource = "rejected"
    raw: dict[str, Any] | None = None


def _singular(word: str) -> str:
    w = word.casefold().strip()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    # classes / processes — not "expenses" (…enses) or "bases" (…ases)
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _pluralize(word: str) -> str:
    w = word.casefold().strip()
    if not w:
        return w
    if w.endswith("s"):
        return w
    if w.endswith("y") and len(w) > 1 and w[-2] not in "aeiou":
        return w[:-1] + "ies"
    return w + "s"


def _domains_from_text(objective: str) -> list[str]:
    text = objective.casefold()
    found: list[str] = []
    for noun in _DOMAIN_NOUNS:
        if re.search(rf"\b{re.escape(noun)}\b", text):
            base = _singular(noun)
            if base not in found:
                found.append(base)
    # Always include auth surface for product-class apps with login/auth wording
    if re.search(r"\b(auth|login|sign[- ]?in|rbac|session)\b", text) and "user" not in found:
        found.insert(0, "user")
    if not found:
        found = ["record"]
    return found[:12]


def propose_outcome_contract_heuristic(
    *,
    objective_id: str,
    objective: str,
    definition_of_done: tuple[str, ...] | list[str] = (),
) -> OutcomeContractProposal:
    """Deterministic proposal from objective text. Validated + sealed or rejected."""
    if not str(objective or "").strip():
        return OutcomeContractProposal(
            ok=False,
            code="CONTRACT_INVALID",
            detail="empty objective",
            source="rejected",
        )
    domains = _domains_from_text(objective)
    ui_ops: list[str] = []
    api_ops: list[ApiOperationSpecV1] = []
    entities: list[str] = []
    mappings: list[IntegrationMappingV1] = []

    # Auth / session
    if re.search(r"\b(auth|login|sign[- ]?in|session)\b", objective, re.I):
        ui_ops.append("login")
        api_ops.append(ApiOperationSpecV1(id="auth.login", path_or_action="POST /auth/login", inputs=("email", "password")))
        entities.append("users")
        mappings.append(
            IntegrationMappingV1(
                id="map-ui-login",
                from_facet="UI",
                from_id="login",
                to_facet="API",
                to_id="auth.login",
            )
        )
        mappings.append(
            IntegrationMappingV1(
                id="map-api-login-users",
                from_facet="API",
                from_id="auth.login",
                to_facet="DB",
                to_id="users",
            )
        )

    if re.search(r"\b(register|registration|sign[- ]?up)\b", objective, re.I):
        ui_ops.append("register")
        api_ops.append(
            ApiOperationSpecV1(
                id="auth.register",
                path_or_action="POST /auth/register",
                inputs=("email", "password"),
            )
        )
        if "users" not in entities:
            entities.append("users")
        mappings.append(
            IntegrationMappingV1(
                id="map-ui-register",
                from_facet="UI",
                from_id="register",
                to_facet="API",
                to_id="auth.register",
            )
        )
        mappings.append(
            IntegrationMappingV1(
                id="map-api-register-users",
                from_facet="API",
                from_id="auth.register",
                to_facet="DB",
                to_id="users",
            )
        )

    if re.search(r"\b(dashboard|kpi)\b", objective, re.I):
        ui_ops.append("view_dashboard")
        api_ops.append(ApiOperationSpecV1(id="dashboard.get", path_or_action="GET /dashboard"))
        if "audit" not in entities:
            entities.append("audit_logs")
        mappings.append(
            IntegrationMappingV1(
                id="map-ui-dashboard",
                from_facet="UI",
                from_id="view_dashboard",
                to_facet="API",
                to_id="dashboard.get",
            )
        )
        mappings.append(
            IntegrationMappingV1(
                id="map-api-dashboard-audit",
                from_facet="API",
                from_id="dashboard.get",
                to_facet="DB",
                to_id="audit_logs",
            )
        )

    for domain in domains:
        entity = _pluralize(domain)
        if entity == "users" and "users" in entities:
            continue
        list_ui = f"list_{entity}"
        create_ui = f"create_{_singular(entity)}"
        list_api = f"{entity}.list"
        create_api = f"{entity}.create"
        ui_ops.extend([list_ui, create_ui])
        api_ops.append(ApiOperationSpecV1(id=list_api, path_or_action=f"GET /{entity}"))
        api_ops.append(
            ApiOperationSpecV1(id=create_api, path_or_action=f"POST /{entity}", inputs=("payload",))
        )
        if entity not in entities:
            entities.append(entity)
        mappings.extend(
            [
                IntegrationMappingV1(
                    id=f"map-ui-{list_ui}",
                    from_facet="UI",
                    from_id=list_ui,
                    to_facet="API",
                    to_id=list_api,
                ),
                IntegrationMappingV1(
                    id=f"map-ui-{create_ui}",
                    from_facet="UI",
                    from_id=create_ui,
                    to_facet="API",
                    to_id=create_api,
                ),
                IntegrationMappingV1(
                    id=f"map-api-{list_api}",
                    from_facet="API",
                    from_id=list_api,
                    to_facet="DB",
                    to_id=entity,
                ),
                IntegrationMappingV1(
                    id=f"map-api-{create_api}",
                    from_facet="API",
                    from_id=create_api,
                    to_facet="DB",
                    to_id=entity,
                ),
            ]
        )

    # Deduplicate while preserving order
    ui_ops = list(dict.fromkeys(ui_ops))
    api_seen: set[str] = set()
    api_unique: list[ApiOperationSpecV1] = []
    for op in api_ops:
        if op.id not in api_seen:
            api_seen.add(op.id)
            api_unique.append(op)
    entities = list(dict.fromkeys(entities))

    dod = tuple(str(x).strip() for x in (definition_of_done or ()) if str(x).strip())
    if not dod:
        dod = (f"Deliver integrated UI/API/DB for: {objective.strip()[:240]}",)

    need_ui = bool(re.search(r"\b(frontend|ui|html|css|js|dashboard|page)\b", objective, re.I)) or True
    need_api = bool(re.search(r"\b(api|backend|php|endpoint|crud)\b", objective, re.I)) or True
    need_db = bool(re.search(r"\b(database|sql|schema|mysql|postgres)\b", objective, re.I)) or True

    # Product-class always requires the three primary facets when heuristic runs
    if is_product_class_objective(objective=objective):
        need_ui = need_api = need_db = True

    raw = {
        "definition_of_done": list(dod),
        "ui_operations": ui_ops if need_ui else [],
        "api_operations": [op.model_dump() for op in api_unique] if need_api else [],
        "db_entities": entities if need_db else [],
        "mappings": [m.model_dump() for m in mappings],
        "source": "heuristic",
    }
    return seal_from_raw_proposal(
        objective_id=objective_id,
        raw=raw,
        source="heuristic",
        need_auth=bool(re.search(r"\b(auth|login|rbac)\b", objective, re.I)),
    )


def seal_from_raw_proposal(
    *,
    objective_id: str,
    raw: dict[str, Any],
    source: ProposalSource,
    need_auth: bool = False,
) -> OutcomeContractProposal:
    """Parse raw proposal dict → validate → seal. Fail closed on invalid shape."""
    try:
        dod = tuple(str(x) for x in (raw.get("definition_of_done") or []) if str(x).strip())
        if not dod:
            dod = ("Outcome contract derived from objective",)
        ui_ops = tuple(str(x) for x in (raw.get("ui_operations") or []) if str(x).strip())
        ui_views = tuple(str(x) for x in (raw.get("ui_views") or []) if str(x).strip())
        api_raw = raw.get("api_operations") or []
        api_ops: list[ApiOperationSpecV1] = []
        for item in api_raw:
            if isinstance(item, str):
                api_ops.append(ApiOperationSpecV1(id=item, path_or_action=item))
            elif isinstance(item, dict) and item.get("id"):
                api_ops.append(
                    ApiOperationSpecV1(
                        id=str(item["id"]),
                        method=(str(item["method"]) if item.get("method") else None),
                        path_or_action=str(item.get("path_or_action") or item["id"]),
                        inputs=tuple(item.get("inputs") or ()),
                        outputs=tuple(item.get("outputs") or ()),
                    )
                )
        entities = tuple(str(x) for x in (raw.get("db_entities") or []) if str(x).strip())
        mappings_raw = raw.get("mappings") or []
        mappings: list[IntegrationMappingV1] = []
        for i, item in enumerate(mappings_raw):
            if not isinstance(item, dict):
                continue
            mappings.append(
                IntegrationMappingV1(
                    id=str(item.get("id") or f"map-{i}"),
                    from_facet=str(item["from_facet"]),  # type: ignore[arg-type]
                    from_id=str(item["from_id"]),
                    to_facet=str(item["to_facet"]),  # type: ignore[arg-type]
                    to_id=str(item["to_id"]),
                )
            )
        auth_actors = tuple(str(x) for x in (raw.get("auth_actors") or []) if str(x).strip())
        auth_perms = tuple(str(x) for x in (raw.get("auth_permissions") or []) if str(x).strip())
        if need_auth and not auth_actors:
            auth_actors = ("authenticated_user",)
            auth_perms = auth_perms or ("session",)

        draft = OutcomeContractV1(
            objective_id=objective_id,
            definition_of_done=dod,
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=ui_ops,
                    required_views=ui_views,
                    acceptance_criteria=("UI provides declared operations",),
                    requirement="MANDATORY" if ui_ops else "ABSENT",
                ),
                API=ApiFacetV1(
                    required_operations=tuple(api_ops),
                    acceptance_criteria=("API provides declared operations",),
                    requirement="MANDATORY" if api_ops else "ABSENT",
                ),
                DB=DbFacetV1(
                    entities=entities,
                    acceptance_criteria=("DB provides declared entities",),
                    requirement="MANDATORY" if entities else "ABSENT",
                ),
                AUTH=AuthFacetV1(
                    actors=auth_actors,
                    permissions=auth_perms,
                    acceptance_criteria=("Auth model declared",) if auth_actors else (),
                    requirement="MANDATORY" if auth_actors else "OPTIONAL",
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=tuple(mappings),
                    acceptance_criteria=("UI→API→DB mappings compose",),
                    requirement="MANDATORY" if mappings else "ABSENT",
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — proposal parse must fail closed
        return OutcomeContractProposal(
            ok=False,
            code="CONTRACT_INVALID",
            detail=f"proposal parse failed: {exc}",
            source="rejected",
            raw=raw,
        )

    ok, code, details = validate_outcome_contract_structure(draft)
    if not ok:
        return OutcomeContractProposal(
            ok=False,
            code=code,
            detail="; ".join(details) or "contract invalid",
            source="rejected",
            raw=raw,
        )
    sealed = seal_outcome_contract(draft)
    return OutcomeContractProposal(
        ok=True,
        code="CONTRACT_SEALED",
        detail="outcome contract validated and sealed",
        contract=sealed,
        source=source,
        raw=raw,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


async def propose_and_seal_outcome_contract(
    *,
    objective_id: str,
    objective: str,
    definition_of_done: tuple[str, ...] | list[str] = (),
    tenant_id: str = "",
    actor_id: str = "",
    authorization_header: str = "",
    use_llm: bool = True,
    timeout_seconds: float = 60.0,
    allow_heuristic_fallback: bool = True,
) -> OutcomeContractProposal:
    """Propose via LLM when allowed; seal only via control plane.

    Fail-closed rules:
    - LLM returned parseable JSON that fails validation → REJECT (no silent heuristic repair).
    - LLM unavailable / empty / unparseable → optional heuristic only if allow_heuristic_fallback.
    - Heuristic is an availability path, not a repair path for bad model output.
    """
    llm_attempted = False
    llm_failure_detail = ""
    if use_llm:
        try:
            from backend.infrastructure.model_gateway.hyeve_integration_client import (
                ChatMessage,
                chat_completion,
            )
            from backend.infrastructure.model_gateway.intelligence_layer.model_registry import (
                load_model_registry,
            )
            from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter

            if not str(authorization_header or "").strip():
                llm_attempted = True
                llm_failure_detail = "AUTHORIZATION_REQUIRED for live OutcomeContract proposal"
            else:
                registry = load_model_registry()
                model_ref = registry.model_ref_for("planner")
                gpr = GovernedProviderRouter().resolve(role="planner", model_ref=model_ref)
                if gpr.disposition != "ALLOW":
                    llm_attempted = True
                    llm_failure_detail = (
                        f"GPR_{gpr.disposition}:{','.join(getattr(gpr, 'reason_codes', ()) or ())}"
                    )
                else:
                    llm_attempted = True
                    user = {
                        "objective_id": objective_id,
                        "objective": objective,
                        "definition_of_done": list(definition_of_done or ()),
                    }
                    resp = await chat_completion(
                        messages=[
                            ChatMessage(
                                role="system",
                                content=OUTCOME_CONTRACT_PROPOSER_SYSTEM_PROMPT,
                            ),
                            ChatMessage(role="user", content=json.dumps(user, sort_keys=True)),
                        ],
                        tenant_id=str(tenant_id or ""),
                        actor_id=str(actor_id or ""),
                        authorization_header=str(authorization_header),
                        response_format={"type": "json_object"},
                        model=model_ref,
                        temperature=0.1,
                        timeout_seconds=timeout_seconds,
                        metadata={
                            "source": "aix.outcome_contract_proposer",
                            "aix_role": "outcome_contract_proposer",
                        },
                    )
                    content = str(getattr(resp, "output_text", "") or "")
                    llm_raw = _extract_json_object(content)
                    if llm_raw is not None:
                        sealed = seal_from_raw_proposal(
                            objective_id=objective_id,
                            raw=llm_raw,
                            source="llm",
                            need_auth=bool(re.search(r"\b(auth|login|rbac)\b", objective, re.I)),
                        )
                        if sealed.ok:
                            return sealed
                        # Parseable but invalid → fail closed. Do not widen via heuristic.
                        return OutcomeContractProposal(
                            ok=False,
                            code=sealed.code,
                            detail=(
                                "LLM OutcomeContract proposal failed deterministic validation; "
                                f"rejecting without heuristic repair: {sealed.detail}"
                            ),
                            source="rejected",
                            raw=llm_raw,
                        )
                    llm_failure_detail = "LLM returned empty or non-JSON OutcomeContract proposal"
        except Exception as exc:  # noqa: BLE001 — availability failure; optional heuristic
            llm_attempted = True
            llm_failure_detail = f"{type(exc).__name__}:{exc}"

    if not allow_heuristic_fallback:
        return OutcomeContractProposal(
            ok=False,
            code="OUTCOME_CONTRACT_REQUIRED",
            detail=(
                llm_failure_detail
                or (
                    "no sealed OutcomeContract (LLM unavailable or empty; heuristic fallback disabled)"
                    if llm_attempted
                    else "heuristic fallback disabled and use_llm=False"
                )
            ),
            source="rejected",
        )

    heuristic = propose_outcome_contract_heuristic(
        objective_id=objective_id,
        objective=objective,
        definition_of_done=definition_of_done,
    )
    if heuristic.ok:
        return heuristic
    return OutcomeContractProposal(
        ok=False,
        code=heuristic.code,
        detail=heuristic.detail,
        source="rejected",
        raw=heuristic.raw,
    )


def bind_steps_to_contract(
    *,
    steps: tuple[Any, ...] | list[Any],
    contract: OutcomeContractV1,
) -> tuple[Any, ...]:
    """Attach OutcomeContract identity to steps without manufacturing coverage.

    DoD-002E: file suffixes / generic UI|API|DB tokens must not dump every
    mandatory operation onto a step. Declared refs (002D seed) are preserved;
    at most one uniquely named operation may be inferred from purpose/scope.
    """
    from backend.aix_core.runtime.verification.semantic_binding import (
        narrow_bind_steps_to_contract,
    )

    return narrow_bind_steps_to_contract(steps=steps, contract=contract)


__all__ = [
    "OUTCOME_CONTRACT_PROPOSER_SYSTEM_PROMPT",
    "OutcomeContractProposal",
    "propose_outcome_contract_heuristic",
    "seal_from_raw_proposal",
    "propose_and_seal_outcome_contract",
    "bind_steps_to_contract",
]
