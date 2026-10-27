"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2: canonical objective obligations.

`derive_obligations()` is the mechanical, no-model converter from what a caller
admitted (objective + definition_of_done) into the durable `ObjectiveObligationV1`
set. Deliberately mechanical for this pass -- see obligations.py's own docstring for
why fine-grained NL decomposition (this sprint doc's own CronJob OB-1..OB-5 example)
is left to a future opt-in packet rather than faked with heuristics here.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.obligations import derive_obligations


def test_one_obligation_per_definition_of_done_entry() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="Refactor CronJobController.php",
        definition_of_done=(
            "Group public cron methods by domain",
            "Extract repeated DB/query boilerplate",
            "Remove unused imports",
        ),
    )
    descriptions = [o.description for o in obligations]
    assert "Group public cron methods by domain" in descriptions
    assert "Extract repeated DB/query boilerplate" in descriptions
    assert "Remove unused imports" in descriptions
    # Scope facets from the objective may add obligations (files/backend) — additive.
    assert len(obligations) >= 3
    assert all(o.required is True for o in obligations)
    assert all(o.status == "UNPLANNED" for o in obligations)
    assert all(o.objective_id == "obj-1" for o in obligations)
    assert len({o.id for o in obligations}) == len(obligations)


def test_definition_of_done_ordering_is_preserved_not_reordered() -> None:
    obligations = derive_obligations(
        objective_id="obj-1", objective="x", definition_of_done=("C", "A", "B")
    )
    assert [o.description for o in obligations] == ["C", "A", "B"]


def test_duplicate_definition_of_done_entries_are_deduplicated_case_insensitively() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="x",
        definition_of_done=("Remove unused imports", "remove unused imports", "  ", "Remove unused imports "),
    )
    assert len(obligations) == 1
    assert obligations[0].description == "Remove unused imports"


def test_empty_definition_of_done_falls_back_to_one_objective_text_obligation() -> None:
    obligations = derive_obligations(
        objective_id="obj-1", objective="Add SMTP_PASSWORD support", definition_of_done=()
    )
    assert len(obligations) == 1
    assert obligations[0].description == "Add SMTP_PASSWORD support"
    assert obligations[0].source == "objective_text"
    assert obligations[0].required is True


def test_atomic_timeout_change_is_one_obligation() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="Change the existing retry timeout from 30 to 60 while preserving all other configuration.",
        definition_of_done=(),
    )
    assert len(obligations) == 1
    assert "timeout" in obligations[0].description.casefold()
    assert obligations[0].verification_strategy


def test_config_implementation_and_tests_are_three_obligations() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="Add a configurable retry limit, make the service consume it, and add tests proving the configured behavior.",
        definition_of_done=(),
    )
    assert len(obligations) == 3
    blob = " ".join(o.description.casefold() for o in obligations)
    assert "configur" in blob or "retry limit" in blob
    assert "service" in blob or "consume" in blob
    assert "test" in blob
    assert len({o.description for o in obligations}) == 3
    assert all(o.verification_strategy for o in obligations)


def test_api_service_and_tests_are_at_least_three_obligations() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="Add an endpoint for retrying failed integration jobs, reuse existing status update behavior, and cover it with tests.",
        definition_of_done=(),
    )
    assert len(obligations) >= 3


def test_refactor_with_preserve_interface_splits_behavioral_obligations() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="Refactor the integration service while preserving the existing public interface.",
        definition_of_done=(),
    )
    assert len(obligations) >= 2
    blob = " ".join(o.description.casefold() for o in obligations)
    assert "interface" in blob
    assert "service" in blob or "refactor" in blob
    assert derive_obligations(objective_id="obj-1", objective="", definition_of_done=()) == ()


def test_definition_of_done_never_silently_collapsed_to_objective_string() -> None:
    """P0's own documented finding #1: prior code silently replaced an explicit DoD
    with the raw objective string. `derive_obligations` must prefer the real DoD."""
    obligations = derive_obligations(
        objective_id="obj-1",
        objective="Some vague objective text nobody should see reflected below",
        definition_of_done=("Only this real requirement",),
    )
    assert len(obligations) == 1
    assert obligations[0].description == "Only this real requirement"
    assert "vague objective text" not in obligations[0].description


def test_read_only_audit_stays_one_obligation() -> None:
    objective = (
        "Read-only audit of the AIX codebase covering backend, tests, and frontend. "
        "Do not modify production code. Write findings to AIX-CURRENT-CODEBASE-AUDIT.md."
    )
    obligations = derive_obligations(
        objective_id="obj-1", objective=objective, definition_of_done=()
    )
    assert len(obligations) == 1
    assert "do not modify" in obligations[0].description.casefold()


def test_oxford_comma_noun_list_does_not_split_without_action_verb() -> None:
    from backend.aix_core.runtime.objective.obligations import split_behavioral_requirements

    parts = split_behavioral_requirements(
        "Map live routers spanning backend, tests, and frontend surfaces."
    )
    assert len(parts) == 1
