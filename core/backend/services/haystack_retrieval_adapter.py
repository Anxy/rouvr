from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from importlib import import_module

try:  # pragma: no cover - optional dependency
    _haystack_module = import_module("haystack")
    HaystackPipeline: Any | None = getattr(_haystack_module, "Pipeline", None)
except Exception:  # pragma: no cover
    HaystackPipeline = None


@dataclass
class HaystackRetrievalAdapter:
    top_k: int = 5
    pipeline: Any | None = None

    @property
    def sdk_available(self) -> bool:
        return HaystackPipeline is not None

    def rank_context_chunks(
        self,
        *,
        query: str | None,
        entities: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        normalized_query = str(query or "").strip().lower()
        ranked: list[dict[str, Any]] = []

        if self.pipeline is not None and self.sdk_available:  # pragma: no cover - integration path
            docs = [
                {
                    "content": str(entity.get("qualified_name") or entity.get("name") or ""),
                    "meta": {
                        "entity_id": entity.get("id"),
                        "repo_id": entity.get("repo_id"),
                        "file_id": entity.get("file_id"),
                    },
                }
                for entity in entities
            ]
            result = self.pipeline.run(data={"query": query or "", "documents": docs})
            items = result.get("documents", []) if isinstance(result, dict) else []
            for idx, item in enumerate(items[: self.top_k]):
                ranked.append(
                    {
                        "rank": idx + 1,
                        "text": str(item.get("content") or ""),
                        "score": float(item.get("score") or 0.0),
                        "source": item.get("meta", {}),
                    }
                )
        else:
            for idx, citation in enumerate(citations):
                file_path = str(citation.get("file_path") or "")
                confidence = float(citation.get("confidence") or 0.0)
                relevance = confidence
                if normalized_query and normalized_query in file_path.lower():
                    relevance += 0.1
                ranked.append(
                    {
                        "rank": idx + 1,
                        "text": file_path or "repo_context",
                        "score": round(relevance, 4),
                        "source": {
                            "repo_id": citation.get("repo_id"),
                            "file_path": file_path or None,
                            "start_line": citation.get("start_line"),
                            "end_line": citation.get("end_line"),
                            "snapshot_id": citation.get("snapshot_id"),
                            "commit_sha": citation.get("commit_sha"),
                        },
                    }
                )
            ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
            ranked = ranked[: self.top_k]
            for idx, item in enumerate(ranked):
                item["rank"] = idx + 1

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        source_refs = [item.get("source") for item in ranked if isinstance(item.get("source"), dict)]
        return {
            "adapter": "haystack" if self.sdk_available else "haystack_fallback",
            "retrieval_ms": elapsed_ms,
            "corpus_hits": len(citations),
            "topk_returned": len(ranked),
            "ranked_chunks": ranked,
            "source_refs": source_refs,
        }
