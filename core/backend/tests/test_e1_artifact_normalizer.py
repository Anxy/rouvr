"""
E1 — Artifact Normalizer Tests
================================
Verifies the acceptance criteria from tasks.md Step 1:

  AC1: Given a raw artifact, the normalizer produces a stable semantic fixture.
  AC2: Running the normalizer twice on the same artifact produces identical output.
  AC3: Two artifacts from different runs of the same scenario produce identical
       output (modulo semantic differences that are explicitly excluded).
  AC4: The fixture is human-readable — no opaque binary blobs.

Additionally:
  - UUID / SHA1 cross-referencing: same source value → same token everywhere.
  - Absolute paths are replaced.
  - Timestamps are replaced with sequence tokens.
  - Evidence refs (flat strings) are unified to typed objects before structural pass.
  - Non-order-sensitive lists are sorted.
"""
import copy
import json
import re
from pathlib import Path

from backend.tests.artifact_normalizer import ArtifactNormalizer, _convert_ref_list

# Most recent three complete runs of task #136 (codeintel project).
# All are retries of the same WH ticket — identical ticket_id, different
# run-specific IDs (invocation, attestation, transition, timestamps).
_ARTIFACTS_DIR = Path(__file__).parents[3] / "artifacts" / "run_fire"
_RUN_LATEST = "20260319-152105-codeintel-136"
_RUN_PRECurrent = "20260319-141422-codeintel-136"
_RUN_PREV2 = "20260319-140557-codeintel-136"


def _load(run_id: str) -> dict:
    return json.loads((_ARTIFACTS_DIR / run_id / "response.json").read_text())


# ---------------------------------------------------------------------------
# AC2: Idempotency — same input → same output on repeated calls
# ---------------------------------------------------------------------------


def test_normalizer_is_idempotent_on_latest_artifact() -> None:
    raw = _load(_RUN_LATEST)
    n = ArtifactNormalizer()
    assert n.normalize(raw) == n.normalize(raw)


def test_normalizer_does_not_mutate_input() -> None:
    raw = _load(_RUN_LATEST)
    original = copy.deepcopy(raw)
    ArtifactNormalizer().normalize(raw)
    assert raw == original


# ---------------------------------------------------------------------------
# Structural pass — UUIDs replaced
# ---------------------------------------------------------------------------


def test_normalizer_replaces_ticket_uuid() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    r = result["result"]["data"]["result"]
    att = r["attestation"]
    assert _UUID_RE_PY.fullmatch(att["ticket_id"]) is None, "Raw UUID must not survive normalization"
    assert att["ticket_id"].startswith("<id:ticket:")


def test_normalizer_cross_references_ticket_uuid() -> None:
    """The same ticket UUID in attestation, transition_record, and result.result
    must all normalize to the same token."""
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    r = result["result"]["data"]["result"]
    token_att = r["attestation"]["ticket_id"]
    token_tr = r["transition_record"]["ticket_id"]
    token_eu_att = r["attestation"]["execution_unit_id"]
    token_eu_tr = r["transition_record"]["execution_unit_id"]
    token_task = r["result"]["task_id"]
    assert (
        token_att == token_tr == token_eu_att == token_eu_tr == token_task
    ), "Same source UUID must produce the same normalized token across all occurrences"


def test_normalizer_replaces_attestation_hash() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    r = result["result"]["data"]["result"]
    att_id = r["attestation"]["attestation_id"]
    assert not _SHA1_RE_PY.fullmatch(att_id), "Raw SHA1 must not survive normalization"
    assert "<hash:attestation:" in att_id


def test_normalizer_cross_references_attestation_hash() -> None:
    """attestation.attestation_id and transition_record.attestation_id are the
    same hash and must normalize to the same token."""
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    r = result["result"]["data"]["result"]
    assert r["attestation"]["attestation_id"] == r["transition_record"]["attestation_id"]


# ---------------------------------------------------------------------------
# Structural pass — absolute paths replaced
# ---------------------------------------------------------------------------


def test_normalizer_replaces_workspace_root_path() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    ws = result["result"]["data"]["result"]["workspace"]["proposal_workspace"]
    assert not ws["workspace_root"].startswith("/"), "Absolute path must be replaced"
    assert "<path:" in ws["workspace_root"]


def test_normalizer_replaces_evidence_ref_paths() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    att = result["result"]["data"]["result"]["attestation"]
    for ref in att["evidence_refs"]:
        assert isinstance(ref, dict)
        ref_id = ref["ref_id"]
        # Raw absolute paths must be gone; bare UUIDs are legitimately replaced with id: tokens
        assert not ref_id.startswith("/"), f"Absolute path in ref_id must be replaced: {ref_id!r}"
        assert ref_id.startswith("<"), f"ref_id must be a stable token, got: {ref_id!r}"


# ---------------------------------------------------------------------------
# Structural pass — timestamps replaced and sequenced
# ---------------------------------------------------------------------------


def test_normalizer_replaces_timestamps() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    tr = result["result"]["data"]["result"]["transition_record"]
    ts = tr["timestamp_utc"]
    assert "<ts:" in ts, f"Timestamp must be replaced with ts: token, got: {ts!r}"
    assert not _TIMESTAMP_RAW.search(ts), f"Raw timestamp must not survive: {ts!r}"


def test_timestamp_tokens_are_sequential_integers() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)

    ts_tokens = set(re.findall(r"<ts:(\d+)>", json.dumps(result)))
    nums = sorted(int(n) for n in ts_tokens)
    assert nums == list(
        range(1, len(nums) + 1)
    ), f"Timestamp tokens must be sequential integers starting at 1, got: {nums}"


# ---------------------------------------------------------------------------
# Evidence ref unification
# ---------------------------------------------------------------------------


def test_normalizer_converts_flat_evidence_refs_to_typed() -> None:
    """result.result.evidence_refs (old flat strings) must become typed objects."""
    raw = _load(_RUN_LATEST)
    # Confirm raw is flat strings before normalization
    inner_raw = raw["result"]["data"]["result"]["result"]["evidence_refs"]
    assert isinstance(inner_raw[0], str), "Pre-condition: raw evidence_refs must be flat strings"

    result = ArtifactNormalizer().normalize(raw)
    inner = result["result"]["data"]["result"]["result"]["evidence_refs"]
    for ref in inner:
        assert isinstance(ref, dict), f"All evidence_refs must be typed dicts after normalization; got {type(ref)}"
        assert "ref_type" in ref
        assert "owned_by" in ref
        assert "ref_id" in ref


def test_unify_ref_list_infers_diff_type() -> None:
    refs = _convert_ref_list(["/home/user/proposals/task-1/diff.patch"])
    assert refs[0]["ref_type"] == "diff"
    assert refs[0]["owned_by"] == "PACK"


def test_unify_ref_list_infers_workspace_status_type() -> None:
    refs = _convert_ref_list(["/home/user/proposals/task-1/changed_files.json"])
    assert refs[0]["ref_type"] == "workspace_status"


def test_unify_ref_list_infers_control_plane_owned() -> None:
    refs = _convert_ref_list(["/app/_runlogs/pkt-outcome-abc123.json"])
    assert refs[0]["owned_by"] == "CONTROL_PLANE"
    assert refs[0]["pack_id"] is None


def test_already_typed_evidence_refs_unchanged_shape() -> None:
    """Typed evidence_refs in attestation must pass through unification unmodified.
    Shape fields (ref_type, owned_by, pack_id) are preserved — only ref_id tokenized.
    The list is sorted (evidence_refs is unordered), so compare as sets.
    """
    raw = _load(_RUN_LATEST)
    att_refs_before = raw["result"]["data"]["result"]["attestation"]["evidence_refs"]
    assert isinstance(att_refs_before[0], dict), "Pre-condition: attestation evidence_refs already typed"

    result = ArtifactNormalizer().normalize(raw)
    att_refs_after = result["result"]["data"]["result"]["attestation"]["evidence_refs"]
    # Compare shape fields as a multiset (order may change due to sorting)
    before_shapes = sorted((r["ref_type"], r["owned_by"]) for r in att_refs_before)
    after_shapes = sorted((r["ref_type"], r["owned_by"]) for r in att_refs_after)
    assert before_shapes == after_shapes, (
        f"ref_type/owned_by multiset must be preserved after normalization: " f"{before_shapes} != {after_shapes}"
    )


# ---------------------------------------------------------------------------
# Content pass
# ---------------------------------------------------------------------------


def test_content_pass_strips_embedded_paths_from_reason_summary() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    r = result["result"]["data"]["result"]
    summary = r["attestation"]["reason_summary"]
    # Must not contain raw absolute paths
    assert not summary.startswith("/"), f"reason_summary should not start with path: {summary!r}"
    # Semantic meaning should be preserved (non-empty)
    assert summary.strip()


# ---------------------------------------------------------------------------
# AC3: Cross-run structural invariants (two runs, same ticket)
# ---------------------------------------------------------------------------


def test_cross_run_attestation_structural_fields_match() -> None:
    """Two runs of the same task produce identical attestation structural fields
    after normalization (pack_id, attester_role, attestation_type — not the hash
    IDs or timestamps which are replaced with different tokens)."""
    n = ArtifactNormalizer()
    r1 = n.normalize(_load(_RUN_PRECurrent))["result"]["data"]["result"]
    r2 = n.normalize(_load(_RUN_PREV2))["result"]["data"]["result"]

    stable_keys = {"pack_id", "attester_role", "attestation_type", "verdict"}
    for key in stable_keys:
        assert r1["attestation"][key] == r2["attestation"][key], (
            f"attestation.{key} should match across runs: " f"{r1['attestation'][key]!r} != {r2['attestation'][key]!r}"
        )


def test_cross_run_transition_state_fields_match() -> None:
    """Two runs of the same task have the same transition prior/new states."""
    n = ArtifactNormalizer()
    r1 = n.normalize(_load(_RUN_PRECurrent))["result"]["data"]["result"]
    r2 = n.normalize(_load(_RUN_PREV2))["result"]["data"]["result"]

    for key in ("prior_state", "new_state", "stage", "actor"):
        assert r1["transition_record"][key] == r2["transition_record"][key], (
            f"transition_record.{key} should match: "
            f"{r1['transition_record'][key]!r} != {r2['transition_record'][key]!r}"
        )


def test_cross_run_ticket_tokens_match() -> None:
    """Same ticket UUID in both runs produces the same ticket token (cross-run
    identity preserved — these runs all target the same WH task #136)."""
    n = ArtifactNormalizer()
    r1 = n.normalize(_load(_RUN_PRECurrent))["result"]["data"]["result"]
    r2 = n.normalize(_load(_RUN_PREV2))["result"]["data"]["result"]
    assert r1["attestation"]["ticket_id"] == r2["attestation"]["ticket_id"]


# ---------------------------------------------------------------------------
# AC4: Human readability
# ---------------------------------------------------------------------------


def test_normalized_fixture_is_json_serializable() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    serialized = json.dumps(result, indent=2)
    assert len(serialized) > 100, "Normalized fixture must not be empty"
    # Validate round-trip
    assert json.loads(serialized) == result


def test_normalized_fixture_has_no_raw_uuids() -> None:
    raw = _load(_RUN_LATEST)
    result = ArtifactNormalizer().normalize(raw)
    serialized = json.dumps(result)
    raw_ticket = raw["result"]["data"]["result"]["attestation"]["ticket_id"]
    assert raw_ticket not in serialized, f"Raw UUID {raw_ticket!r} must not appear in normalized fixture"


_UUID_RE_PY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_SHA1_RE_PY = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_TIMESTAMP_RAW = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})")
