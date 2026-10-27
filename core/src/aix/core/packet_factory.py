from pathlib import Path
from typing import Any


class AIXPacketFactory:
    _TARGET_FILE = "backend/tests/aix_core/bootstrap/test_contract_admission.py"
    _TARGET_SYMBOL = (
        "backend.tests.aix_core.bootstrap.test_contract_admission."
        "test_executor_admits_complete_authoritative_implementation_contract"
    )
    _DEFAULT_ASSERT_WINDOW = "    assert result.admitted is True"

    @classmethod
    def _candidate_roots(cls) -> list[Path]:
        here = Path(__file__).resolve()
        candidates = [Path.cwd(), *here.parents]
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    @classmethod
    def _load_live_assert_window(cls) -> str:
        for root in cls._candidate_roots():
            candidate = root / cls._TARGET_FILE
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8")
            if cls._DEFAULT_ASSERT_WINDOW in text:
                return cls._DEFAULT_ASSERT_WINDOW
            marker = "assert required is True"
            marker_index = text.find(marker)
            if marker_index == -1:
                continue
            start = text.rfind("result = admit_contract(", 0, marker_index)
            if start != -1:
                end = marker_index + len(marker)
                return text[start:end].strip("\n")
        return cls._DEFAULT_ASSERT_WINDOW

    @classmethod
    def create_strict_planner_packet(cls, task_number: int) -> dict[str, Any]:
        preloaded_source = cls._load_live_assert_window()
        compact_snippet = "\n".join(preloaded_source.splitlines()[:10]).strip()
        return {
            "schema_name": "aix.planner.slice_packet.v2",
            "schema_version": "v2",
            "target_file": cls._TARGET_FILE,
            "target_symbol": cls._TARGET_SYMBOL,
            "task_number": task_number,
            "slice_id": "recovery-test-2573",
            "objective": "Flip the recovery test assertion in one bounded edit.",
            "bounded_mutation_goal": "Change only the final assertion from True to False.",
            "allowed_surface": {
                "anchor": {
                    "symbol": cls._TARGET_SYMBOL,
                    "compact_snippet": compact_snippet,
                }
            },
            "validation_command": "python -m pytest backend/tests/aix_core/bootstrap/test_contract_admission.py -q --tb=short",
            "output_schema": {
                "type": "edit",
                "fields": [
                    "type",
                    "file",
                    "qualified_symbol",
                    "edit_intent",
                    "preserve_constraints",
                    "line_targets",
                    "command",
                ],
            },
            "hard_failure_rules": ["insufficient context -> fail immediately with reason"],
        }
