from __future__ import annotations

from backend.services.haystack_retrieval_adapter import HaystackRetrievalAdapter


def test_haystack_adapter_fallback_ranking_and_provenance() -> None:
    adapter = HaystackRetrievalAdapter(top_k=2, pipeline=None)
    result = adapter.rank_context_chunks(
        query="app.py",
        entities=[
            {"id": "ent-1", "qualified_name": "pkg.fn"},
            {"id": "ent-2", "qualified_name": "pkg.cls"},
        ],
        citations=[
            {
                "repo_id": "repo-1",
                "file_path": "src/app.py",
                "start_line": 10,
                "end_line": 14,
                "snapshot_id": "snap-1",
                "commit_sha": "abc",
                "confidence": 0.8,
            },
            {
                "repo_id": "repo-1",
                "file_path": "src/other.py",
                "start_line": 20,
                "end_line": 25,
                "snapshot_id": "snap-1",
                "commit_sha": "abc",
                "confidence": 0.6,
            },
        ],
    )

    assert result["adapter"] in {"haystack_fallback", "haystack"}
    assert result["topk_returned"] == 2
    assert result["corpus_hits"] == 2
    assert len(result["ranked_chunks"]) == 2
    assert result["ranked_chunks"][0]["source"]["file_path"] == "src/app.py"
    assert len(result["source_refs"]) == 2
