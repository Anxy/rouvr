"""C023 context manifest tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.objective_contract.context_manifest import (
    ContextManifestError,
    add_item,
    assemble_prompt_parts,
    new_manifest,
    reconstruct_manifest,
)


def test_c023_secret_cannot_embed_and_prompt_is_redacted() -> None:
    m = new_manifest(tenant_id="t", run_id="cyc_1")
    add_item(m, role="objective", source="user", content="Add /health endpoint")
    with pytest.raises(ContextManifestError) as exc:
        add_item(m, role="working", source="note", content="password=hunter2")
    assert exc.value.code == "SECRET_EMBEDDED_IN_CONTEXT"
    add_item(
        m,
        role="working",
        source="vault",
        content="placeholder",
        trust="secret_ref",
        uri="secret://db_password",
    )
    parts = assemble_prompt_parts(m)
    joined = " ".join(p["text"] for p in parts)
    assert "hunter2" not in joined
    assert "SECRET_REF" in joined
    roundtrip = reconstruct_manifest(m.to_dict())
    assert roundtrip.manifest_id == m.manifest_id


def test_c023_stale_and_untrusted_excluded_from_prompt() -> None:
    m = new_manifest(tenant_id="t")
    add_item(m, role="plan", source="plan", content="step 1")
    add_item(m, role="repository", source="web", content="random blog", trust="untrusted")
    add_item(m, role="repository", source="old", content="old file", trust="stale")
    parts = assemble_prompt_parts(m)
    assert len(parts) == 1
    assert parts[0]["role"] == "plan"
