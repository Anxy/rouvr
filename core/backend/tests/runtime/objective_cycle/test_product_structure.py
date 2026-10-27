"""AIX-PD-003A: OutcomeContract → deterministic ProductStructure compiler."""

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
from backend.aix_core.runtime.objective.outcome_obligations import compile_outcome_obligations
from backend.aix_core.runtime.objective.product_structure import (
    compile_product_structure,
    product_structure_canonical_json,
)


def _attendance_sealed() -> OutcomeContractV1:
    artifact = (
        Path(__file__).resolve().parents[3]
        / ".tmp_llm_vs_heuristic_attendance.json"
    )
    if artifact.is_file():
        raw = json.loads(artifact.read_text(encoding="utf-8"))["llm_no_fallback"]["raw"]
        proposal = seal_from_raw_proposal(
            objective_id="obj_att_pd003a",
            raw=raw,
            source="llm",
            need_auth=True,
        )
        assert proposal.ok and proposal.contract is not None
        return proposal.contract

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
            objective_id="obj_att_pd003a",
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


def test_view_op_pairing_follows_contract_declaration_order() -> None:
    """Equal-length views/ops pair by contract order, not alphabetical zip."""
    m1 = IntegrationMappingV1(
        id="map-0", from_facet="UI", from_id="register_student", to_facet="API", to_id="students.create"
    )
    m2 = IntegrationMappingV1(
        id="map-1", from_facet="API", from_id="students.create", to_facet="DB", to_id="students"
    )
    sealed = seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_pair_order",
            definition_of_done=("pairing",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("register_student", "view_attendance", "submit_attendance"),
                    required_views=("student_registration", "attendance_view", "attendance_submission"),
                    acceptance_criteria=("ui",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="students.create", path_or_action="POST /students"),
                    ),
                    acceptance_criteria=("api",),
                ),
                DB=DbFacetV1(entities=("students",), acceptance_criteria=("db",)),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(m1, m2),
                    acceptance_criteria=("map",),
                ),
            ),
        )
    )
    result = compile_product_structure(sealed)
    assert result.ok and result.structure is not None
    by_ref = {v.ref: v.ui_operation_refs for v in result.structure.views}
    assert by_ref["student_registration"] == ("register_student",)
    assert by_ref["attendance_view"] == ("view_attendance",)
    assert by_ref["attendance_submission"] == ("submit_attendance",)


def test_unsealed_contract_rejected() -> None:
    sealed = _attendance_sealed()
    unsealed = sealed.model_copy(update={"contract_hash": ""})
    result = compile_product_structure(unsealed)
    assert result.ok is False
    assert result.code == "CONTRACT_UNSEALED"
    assert result.structure is None


def test_attendance_compiles_product_structure() -> None:
    contract = _attendance_sealed()
    result = compile_product_structure(contract)
    assert result.ok is True, result.detail
    assert result.code == "PRODUCT_STRUCTURE_COMPILED"
    structure = result.structure
    assert structure is not None
    assert structure.contract_id == contract.contract_id
    assert structure.contract_hash == contract.contract_hash
    assert structure.structure_id.startswith("ps_")

    assert len(structure.api_operations) >= 1
    assert len(structure.db_entities) >= 1
    assert len(structure.views) >= 1
    assert len(structure.flows) >= 1
    assert len(structure.capabilities) >= 1
    assert len(structure.relations) >= 1
    assert structure.obligation_ids == tuple(
        sorted(o.obligation_id for o in compile_outcome_obligations(contract).obligations)
    )

    api_refs = {n.ref for n in structure.api_operations}
    db_refs = {n.ref for n in structure.db_entities}
    assert "students.create" in api_refs or any("student" in r for r in api_refs)
    assert db_refs

    # Flow edges reference structure nodes
    for flow in structure.flows:
        assert flow.from_facet == "UI"
        assert flow.to_facet == "API"


def test_product_structure_deterministic() -> None:
    contract = _attendance_sealed()
    a = compile_product_structure(contract)
    b = compile_product_structure(contract)
    assert a.ok and b.ok
    assert a.structure is not None and b.structure is not None
    assert a.structure.structure_id == b.structure.structure_id
    assert product_structure_canonical_json(a.structure) == product_structure_canonical_json(
        b.structure
    )
    assert a.structure.to_dict() == b.structure.to_dict()


def test_views_from_contract_not_erp_catalog() -> None:
    """Views must come from sealed contract / UI ops — module has no ERP page catalog."""
    module_path = (
        Path(__file__).resolve().parents[3]
        / "aix_core"
        / "runtime"
        / "objective"
        / "product_structure.py"
    )
    source = module_path.read_text(encoding="utf-8")
    forbidden = (
        "erp_dashboard",
        "erp-inventory",
        "ERP_PAGES",
        "hardcoded_erp",
        "/erp/",
        "invoice_list_page",
        "ledger_page_catalog",
    )
    for token in forbidden:
        assert token not in source, f"forbidden product hard-code token {token!r}"

    # Synthetic path when required_views empty
    m_ui_api = IntegrationMappingV1(
        id="map-x", from_facet="UI", from_id="do_thing", to_facet="API", to_id="thing.create"
    )
    m_api_db = IntegrationMappingV1(
        id="map-y", from_facet="API", from_id="thing.create", to_facet="DB", to_id="things"
    )
    sealed = seal_outcome_contract(
        OutcomeContractV1(
            objective_id="obj_synth_views",
            definition_of_done=("generic capability",),
            facets=OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("do_thing",),
                    required_views=(),
                    acceptance_criteria=("ui",),
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="thing.create", path_or_action="POST /things"),
                    ),
                    acceptance_criteria=("api",),
                ),
                DB=DbFacetV1(entities=("things",), acceptance_criteria=("db",)),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(m_ui_api, m_api_db),
                    acceptance_criteria=("map",),
                ),
            ),
        )
    )
    result = compile_product_structure(sealed)
    assert result.ok is True, result.detail
    assert result.structure is not None
    assert len(result.structure.views) == 1
    assert result.structure.views[0].source == "SYNTHETIC_FROM_UI_OPERATION"
    assert result.structure.views[0].ui_operation_refs == ("do_thing",)


def test_relations_link_capability_view_api_db() -> None:
    contract = _attendance_sealed()
    result = compile_product_structure(contract)
    assert result.ok and result.structure is not None
    kinds = {e.kind for e in result.structure.relations}
    assert "CAPABILITY_VIEW" in kinds
    assert "VIEW_UI_OPERATION" in kinds
    assert "FLOW_API_OPERATION" in kinds or "VIEW_API_OPERATION" in kinds


def test_obligation_compiler_regression_still_compiles() -> None:
    """PD-003A must not disturb DoD-002A obligation compile."""
    contract = _attendance_sealed()
    obl = compile_outcome_obligations(contract)
    assert obl.ok is True
    assert obl.code == "OBLIGATIONS_COMPILED"
    assert len(obl.obligations) >= 1
    ps = compile_product_structure(contract)
    assert ps.ok is True
    assert set(ps.structure.obligation_ids) == {o.obligation_id for o in obl.obligations}  # type: ignore[union-attr]
