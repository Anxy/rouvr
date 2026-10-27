from __future__ import annotations

import pytest

from backend.aix_core.intelligence.context.experimental.context_capsule import (
    ContextCapsuleBuilder,
    ContextItem,
    estimate_tokens,
)

HASH = "a" * 64
SHA = "b" * 40


def _item(item_id: str, content: str, *, required: bool = False, priority: int = 0) -> ContextItem:
    return ContextItem(
        item_id=item_id,
        source_uri=f"codeintel://snapshot/s-1/{item_id}",
        provenance_record_sha256=HASH,
        content=content,
        required=required,
        priority=priority,
    )


def _build(items: list[ContextItem], max_tokens: int = 100):
    return ContextCapsuleBuilder().build(
        tenant_id="tenant-1",
        repository_id="aix",
        commit_sha=SHA,
        items=items,
        max_tokens=max_tokens,
    )


def test_capsule_is_deterministic_across_input_order() -> None:
    items = [_item("b", "second", priority=1), _item("a", "first", required=True)]

    left = _build(items)
    right = _build(list(reversed(items)))

    assert left.model_dump(mode="json") == right.model_dump(mode="json")
    assert [item.item_id for item in left.items] == ["a", "b"]


def test_capsule_normalizes_whitespace_and_hashes_result() -> None:
    left = _build([_item("a", "hello   governed\nworld", required=True)])
    right = _build([_item("a", "hello governed world", required=True)])

    assert left.items[0].content == "hello governed world"
    assert left.capsule_sha256 == right.capsule_sha256


def test_capsule_enforces_token_cap_without_partial_item() -> None:
    required = _item("required", "r" * 16, required=True)
    optional = _item("optional", "o" * 16, priority=100)
    capsule = _build([optional, required], max_tokens=estimate_tokens(required.content))

    assert [item.item_id for item in capsule.items] == ["required"]
    assert capsule.omitted_item_ids == ("optional",)
    assert capsule.estimated_tokens <= capsule.max_tokens


def test_capsule_rejects_when_required_context_exceeds_cap() -> None:
    with pytest.raises(ValueError, match="REQUIRED_CONTEXT_EXCEEDS_TOKEN_CAP"):
        _build([_item("required", "required context", required=True)], max_tokens=1)


def test_capsule_deduplicates_equivalent_content() -> None:
    capsule = _build(
        [
            _item("primary", "same context", required=True),
            _item("duplicate", "same  context", priority=100),
        ]
    )

    assert [item.item_id for item in capsule.items] == ["primary"]
    assert capsule.omitted_item_ids == ("duplicate",)


def test_capsule_rejects_duplicate_item_identity() -> None:
    with pytest.raises(ValueError, match="DUPLICATE_CONTEXT_ITEM_ID"):
        _build([_item("same", "one"), _item("same", "two")])


@pytest.mark.parametrize(
    ("tenant_id", "repository_id", "commit_sha"),
    [
        ("", "aix", SHA),
        ("tenant-1", "", SHA),
        ("tenant-1", "aix", "short"),
    ],
)
def test_capsule_requires_scope_identity(tenant_id: str, repository_id: str, commit_sha: str) -> None:
    with pytest.raises(ValueError, match="tenant_id, repository_id and commit_sha"):
        ContextCapsuleBuilder().build(
            tenant_id=tenant_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            items=[_item("a", "content")],
            max_tokens=10,
        )


def test_capsule_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one context item"):
        _build([])
