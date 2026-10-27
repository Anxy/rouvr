from __future__ import annotations

from backend.scripts.aix_planner_slice_shadow_report import build_shadow_snapshot


def test_build_shadow_snapshot_counts_ready_and_clarification_runs() -> None:
    snapshot = build_shadow_snapshot(
        payloads=[
            {
                "run_id": "run-ready",
                "response": {
                    "planner": {
                        "planner_packet_forwarded": True,
                        "planner_packet_metrics": {
                            "status": "ready",
                            "strict_packet_present": True,
                            "clarification_gap_codes": [],
                        },
                        "planner_packet": {
                            "validation_error": None,
                            "slice_packet_current": {"status": "ready"},
                        },
                    },
                    "quality_gate": {"code": "QUALITY_OK"},
                    "final_status": "needs_review",
                },
            },
            {
                "run_id": "run-gap",
                "response": {
                    "planner": {
                        "planner_packet_forwarded": False,
                        "planner_packet_metrics": {
                            "status": "needs_clarification",
                            "strict_packet_present": False,
                            "clarification_gap_codes": [
                                "EXACTLY_ONE_TARGET_FILE_REQUIRED",
                                "CODEINTEL_ANCHOR_REQUIRED",
                            ],
                        },
                        "planner_packet": {
                            "validation_error": "INVALID_SYMBOL_FORMAT",
                            "slice_packet_current": {"status": "needs_clarification"},
                        },
                    },
                    "quality_gate": {"code": "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE"},
                    "final_status": "needs_review",
                },
            },
        ]
    )

    assert snapshot["sample_size"] == 2
    assert snapshot["counts"]["planner_packet_status"]["ready"] == 1
    assert snapshot["counts"]["planner_packet_status"]["needs_clarification"] == 1
    assert snapshot["counts"]["quality_gate_code"]["QUALITY_OK"] == 1
    assert snapshot["counts"]["quality_gate_code"]["PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE"] == 1
    assert snapshot["counts"]["clarification_gap_codes"]["EXACTLY_ONE_TARGET_FILE_REQUIRED"] == 1
    assert snapshot["rates"]["strict_packet_present_rate"] == 0.5
    assert snapshot["rates"]["planner_packet_forwarded_rate"] == 0.5
    assert snapshot["rates"]["ready_rate"] == 0.5
    assert snapshot["runs"][0]["run_id"] == "run-ready"


def test_build_shadow_snapshot_handles_empty_input() -> None:
    snapshot = build_shadow_snapshot(payloads=[])

    assert snapshot["sample_size"] == 0
    assert snapshot["counts"]["planner_packet_status"] == {}
    assert snapshot["rates"]["strict_packet_present_rate"] == 0.0
    assert snapshot["rates"]["planner_packet_forwarded_rate"] == 0.0
    assert snapshot["rates"]["ready_rate"] == 0.0


def test_build_shadow_snapshot_reads_run_fire_envelope_shape() -> None:
    snapshot = build_shadow_snapshot(
        payloads=[
            {
                "run_id": "run-envelope",
                "response": {
                    "result": {
                        "data": {
                            "planner": {
                                "planner_packet_forwarded": True,
                                "planner_packet_metrics": {
                                    "status": "ready",
                                    "strict_packet_present": True,
                                    "clarification_gap_codes": [],
                                },
                                "planner_packet": {
                                    "validation_error": None,
                                    "slice_packet_current": {"status": "ready"},
                                },
                            },
                            "quality_gate": {"code": "QUALITY_OK"},
                            "final_status": "needs_review",
                        }
                    }
                },
            }
        ]
    )

    assert snapshot["sample_size"] == 1
    assert snapshot["counts"]["planner_packet_status"]["ready"] == 1
    assert snapshot["counts"]["quality_gate_code"]["QUALITY_OK"] == 1
    assert snapshot["runs"][0]["final_status"] == "needs_review"


def test_build_shadow_snapshot_reads_nested_run_fire_result_shape() -> None:
    snapshot = build_shadow_snapshot(
        payloads=[
            {
                "run_id": "run-nested-envelope",
                "response": {
                    "result": {
                        "data": {
                            "result": {
                                "planner": {
                                    "planner_packet_forwarded": False,
                                    "planner_packet_metrics": {
                                        "status": "needs_clarification",
                                        "strict_packet_present": False,
                                        "clarification_gap_codes": ["CODEINTEL_ANCHOR_REQUIRED"],
                                    },
                                    "planner_packet": {
                                        "validation_error": None,
                                        "slice_packet_current": {"status": "needs_clarification"},
                                    },
                                },
                                "quality_gate": {"code": "QUALITY_OK"},
                                "final_status": "needs_review",
                            }
                        }
                    }
                },
            }
        ]
    )

    assert snapshot["sample_size"] == 1
    assert snapshot["counts"]["planner_packet_status"]["needs_clarification"] == 1
    assert snapshot["counts"]["clarification_gap_codes"]["CODEINTEL_ANCHOR_REQUIRED"] == 1
    assert snapshot["counts"]["quality_gate_code"]["QUALITY_OK"] == 1
    assert snapshot["runs"][0]["planner_packet_forwarded"] is False
