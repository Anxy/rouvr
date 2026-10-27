"""AIX-DoD-002A/B: OutcomeContract → mandatory obligations compiler."""

from __future__ import annotations

import json
from pathlib import Path

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
)
from backend.aix_core.runtime.objective.outcome_contract_proposer import seal_from_raw_proposal
from backend.aix_core.runtime.objective.outcome_obligations import (
    compile_outcome_obligations,
    obligation_id_for,
)


def _attendance_sealed() -> OutcomeContractV1:
    artifact = (
        Path(__file__).resolve().parents[3]
        / ".tmp_llm_vs_heuristic_attendance.json"
    )
    if artifact.is_file():
        raw = json.loads(artifact.read_text(encoding="utf-8"))["llm_no_fallback"]["raw"]
        proposal = seal_from_raw_proposal(
            objective_id="obj_att_002a",
            raw=raw,
            source="llm",
            need_auth=True,
        )
        assert proposal.ok and proposal.contract is not None
        return proposal.contract

    # Fallback fixture if artifact missing
    m1 = IntegrationMappingV1(
        id="map-0", from_facet="UI", from_id="register_student", to_facet="API", to_id="students.create"
    )
    m2 = IntegrationMappingV1(
        id="map-1", from_facet="UI", from_id="view_attendance", to_facet="API", to_id="attendance.list"
    )
    m3 = IntegrationMappingV1(
        id="map-2", from_facet="UI", from_id="submit_attendance", to_facet="API", to_id="attendance.create"
    )
    m4 = IntegrationMappingV1(
        id="map-3", from_facet="API", from_id="students.create", to_facet="DB", to_id="students"
    )
    m5 = IntegrationMappingV1(
        id="map-4", from_facet="API", from_id="attendance.list", to_facet="DB", to_id="attendance_records"
    )
    m6 = IntegrationMappingV1(
        id="map-5", from_facet="API", from_id="attendance.create", to_facet="DB", to_id="attendance_records"
    )
    return seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_att_002a",
            definition_of_done=("Staff can register students and submit attendance",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("register_student", "view_attendance", "submit_attendance"),
                    required_views=("student_registration", "attendance_view", "attendance_submission"),
                    acceptance_criteria=("UI provides declared operations",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                        ApiOperationSpecV1(id="attendance.list", path_or_action="GET /attendance"),
                        ApiOperationSpecV1(id="attendance.create", path_or_action="POST /attendance"),
                    ),
                    acceptance_criteria=("API provides declared operations",),
                ),
                DB=DbFacetV1(
                    entities=("students", "attendance_records"),
                    acceptance_criteria=("DB provides declared entities",),
                ),
                AUTH=AuthFacetV1(
                    actors=("staff",),
                    permissions=("students.write", "attendance.read", "attendance.write"),
                    requirement="MANDATORY",
                    acceptance_criteria=("Auth model declared",),
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(m1, m2, m3, m4, m5, m6),
                    acceptance_criteria=("UI to API to DB mappings compose",),
                ),
            ),
        )
    )


def test_unsealed_contract_rejected() -> None:
    sealed = _attendance_sealed()
    unsealed = sealed.model_copy(update={"contract_hash": ""})
    result = compile_outcome_obligations(unsealed)
    assert result.ok is False
    assert result.code == "CONTRACT_UNSEALED"
    assert result.obligations == ()


def test_attendance_compiles_mandatory_obligations() -> None:
    contract = _attendance_sealed()
    result = compile_outcome_obligations(contract)
    assert result.ok is True, result.detail
    assert result.code == "OBLIGATIONS_COMPILED"
    ids = [o.obligation_id for o in result.obligations]
    kinds = {o.kind for o in result.obligations}

    assert "obl_ui_register_student" in ids
    assert "obl_ui_view_attendance" in ids
    assert "obl_ui_submit_attendance" in ids
    assert "obl_api_students_create" in ids
    assert "obl_api_attendance_list" in ids
    assert "obl_api_attendance_create" in ids
    assert "obl_db_students" in ids
    assert "obl_db_attendance_records" in ids
    assert "UI_OPERATION" in kinds
    assert "API_OPERATION" in kinds
    assert "DB_ENTITY" in kinds
    assert "INTEGRATION_MAPPING" in kinds

    ui_refs = {o.ref for o in result.obligations if o.kind == "UI_OPERATION"}
    assert ui_refs == {"register_student", "view_attendance", "submit_attendance"}
    api_refs = {o.ref for o in result.obligations if o.kind == "API_OPERATION"}
    assert api_refs == {"students.create", "attendance.list", "attendance.create"}
    db_refs = {o.ref for o in result.obligations if o.kind == "DB_ENTITY"}
    assert db_refs == {"students", "attendance_records"}
    map_count = sum(1 for o in result.obligations if o.kind == "INTEGRATION_MAPPING")
    assert map_count == len(contract.facets.INTEGRATION.mappings)


def test_auth_mandatory_emits_actors_and_permissions() -> None:
    contract = _attendance_sealed()
    if contract.facets.AUTH.requirement != "MANDATORY":
        # Force mandatory auth on a sealed copy path via re-seal
        draft = contract.model_copy(
            update={
                "contract_hash": "",
                "facets": contract.facets.model_copy(
                    update={
                        "AUTH": AuthFacetV1(
                            actors=("staff",),
                            permissions=("attendance.write",),
                            requirement="MANDATORY",
                            acceptance_criteria=("Auth model declared",),
                        )
                    }
                ),
            }
        )
        contract = seal_outcome_contract(draft)
    result = compile_outcome_obligations(contract)
    assert result.ok is True
    auth = [o for o in result.obligations if o.facet == "AUTH"]
    assert auth
    assert any(o.kind == "AUTH_ACTOR" for o in auth)
    assert any(o.kind == "AUTH_PERMISSION" for o in auth)


def test_auth_optional_emits_nothing() -> None:
    contract = _attendance_sealed()
    draft = contract.model_copy(
        update={
            "contract_hash": "",
            "facets": contract.facets.model_copy(
                update={
                    "AUTH": AuthFacetV1(
                        actors=("staff",),
                        permissions=("attendance.write",),
                        requirement="OPTIONAL",
                    )
                }
            ),
        }
    )
    sealed = seal_outcome_contract(draft)
    result = compile_outcome_obligations(sealed)
    assert result.ok is True
    assert all(o.facet != "AUTH" for o in result.obligations)


def test_determinism_same_sealed_contract() -> None:
    contract = _attendance_sealed()
    a = compile_outcome_obligations(contract)
    b = compile_outcome_obligations(contract)
    assert a.ok and b.ok
    assert [o.obligation_id for o in a.obligations] == [o.obligation_id for o in b.obligations]
    assert [o.ref for o in a.obligations] == [o.ref for o in b.obligations]
    for o in a.obligations:
        assert o.contract_id == contract.contract_id
        assert o.contract_hash == contract.contract_hash
        assert o.source == "OUTCOME_CONTRACT_V1"
        assert o.mandatory is True


def test_obligation_id_algorithm() -> None:
    assert obligation_id_for(facet="UI", kind="UI_OPERATION", ref="register_student") == (
        "obl_ui_register_student"
    )
    assert obligation_id_for(facet="API", kind="API_OPERATION", ref="students.create") == (
        "obl_api_students_create"
    )
    assert obligation_id_for(facet="DB", kind="DB_ENTITY", ref="attendance_records") == (
        "obl_db_attendance_records"
    )


def test_structurally_invalid_sealed_rejected() -> None:
    # Seal a contract that fails structure (mapping to unknown UI op) — seal allows hash;
    # validate_outcome_contract_structure should fail at compile.
    bad = seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_bad",
            definition_of_done=("x",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(required_operations=("register",)),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                    )
                ),
                DB=DbFacetV1(entities=("students",)),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(
                        IntegrationMappingV1(
                            id="m1",
                            from_facet="UI",
                            from_id="missing_op",
                            to_facet="API",
                            to_id="students.create",
                        ),
                    )
                ),
            ),
        )
    )
    result = compile_outcome_obligations(bad)
    assert result.ok is False
    assert result.code == "CONTRACT_INVALID"
