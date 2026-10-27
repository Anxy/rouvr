"""CodeIntel query client for lightweight context retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.hyeve_aix_service.config import settings
from backend.services.haystack_retrieval_adapter import HaystackRetrievalAdapter


def _entity_confidence(entity: dict[str, Any], query: str | None) -> float:
    if not query:
        return 0.7
    q = query.strip().lower()
    if not q:
        return 0.7
    name = str(entity.get("name") or "").lower()
    qualified = str(entity.get("qualified_name") or "").lower()
    if q == name or q == qualified:
        return 0.95
    if q in name or q in qualified:
        return 0.85
    return 0.6


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def _query_tokens(query: str | None) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9_]+", str(query or "").lower()) if len(token) >= 3]


def _normalize_likely_test_path(path: str | None) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized:
        return ""
    return re.sub(r"(^|/)tests/test_test_", r"\1tests/test_", normalized)


def _query_identity(*, project_id: str | None, entity_query: str | None, retrieval_mode: str) -> dict[str, str]:
    raw = {
        "project_id": project_id or "",
        "entity_query": entity_query or "",
        "retrieval_mode": retrieval_mode,
    }
    return {
        "entity_query": entity_query or "",
        "query_fingerprint": hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest(),
    }


def _codeintel_transport_headers(base_url: str) -> dict[str, str]:
    parsed = urlparse(str(base_url or ""))
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        return {"X-Forwarded-Proto": "https"}
    return {}


def _summarize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entity.get("id"),
        "repo_id": entity.get("repo_id"),
        "file_path": entity.get("file_path"),
        "snapshot_id": entity.get("snapshot_id"),
        "commit_sha": entity.get("commit_sha"),
        "entity_type": entity.get("entity_type") or entity.get("kind"),
        "name": entity.get("name"),
        "qualified_name": entity.get("qualified_name"),
        "signature": entity.get("signature"),
        "start_line": entity.get("start_line"),
        "end_line": entity.get("end_line"),
        "confidence_score": entity.get("confidence_score"),
        "freshness_status": entity.get("freshness_status"),
        "warnings": list(entity.get("warnings") or []),
        "why_ranked": list(entity.get("why_ranked") or []),
    }


def _hydrate_context_pack_entity(
    entity: dict[str, Any],
    *,
    repo_id: str | None,
    snapshot_id: str | None,
    commit_sha: str | None,
) -> dict[str, Any]:
    hydrated = dict(entity)
    entity_id = str(hydrated.get("entity_id") or hydrated.get("id") or "").strip()
    if entity_id and not hydrated.get("id"):
        hydrated["id"] = entity_id
    if repo_id and not hydrated.get("repo_id"):
        hydrated["repo_id"] = repo_id
    if snapshot_id and not hydrated.get("snapshot_id"):
        hydrated["snapshot_id"] = snapshot_id
    if commit_sha and not hydrated.get("commit_sha"):
        hydrated["commit_sha"] = commit_sha
    return hydrated


def _context_pack_resolution(payload: dict[str, Any]) -> dict[str, Any]:
    resolution = payload.get("resolution")
    return dict(resolution) if isinstance(resolution, dict) else {}


def _bounded_refusal_reason(reason_code: str | None) -> str:
    normalized = str(reason_code or "").strip().upper()
    mapping = {
        "DOCS_ONLY_COVERAGE": "codeintel_docs_only_coverage",
        "QUERY_TOO_BROAD": "codeintel_query_too_broad",
        "NO_INDEX_SNAPSHOT": "codeintel_no_index_snapshot",
    }
    return mapping.get(normalized, "codeintel_bounded_refusal")


def _context_pack_bounded_refusal_policy(payload: dict[str, Any]) -> dict[str, Any] | None:
    resolution = _context_pack_resolution(payload)
    if str(resolution.get("status") or "").strip().lower() != "bounded_refusal":
        return None
    dominant_reason_code = str(resolution.get("dominant_reason_code") or "CODEINTEL_BOUNDED_REFUSAL").strip().upper()
    reason_codes = [
        str(item).strip().upper() for item in list(resolution.get("reason_codes") or []) if str(item).strip()
    ]
    if dominant_reason_code and dominant_reason_code not in reason_codes:
        reason_codes.insert(0, dominant_reason_code)
    return {
        "mode": "bounded_refusal",
        "reason": _bounded_refusal_reason(dominant_reason_code),
        "reason_code": dominant_reason_code,
        "reason_codes": reason_codes,
        "reason_summary": str(resolution.get("reason_summary") or "").strip()
        or "CodeIntel refused grounding for this planner query.",
        "coverage_state": str(resolution.get("coverage_state") or "").strip() or None,
        "query_intent": str(resolution.get("query_intent") or "").strip() or None,
        "can_retry": bool(resolution.get("can_retry")),
        "retry_action": str(resolution.get("retry_action") or "").strip() or None,
        "matched_entity_count": int(resolution.get("matched_entity_count") or 0),
        "code_entity_count": int(resolution.get("code_entity_count") or 0),
        "documentation_entity_count": int(resolution.get("documentation_entity_count") or 0),
        "caller_count": int(resolution.get("caller_count") or 0),
    }


def _build_grounding_bundle(
    *,
    query: str | None,
    primary_repo_id: str | None,
    repo_entities: list[dict[str, Any]],
    repo_file_paths: list[str],
    impact_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    tokens = _query_tokens(query)
    impact_data = dict((impact_payload or {}).get("data") or {})
    impact_nodes = [item for item in impact_data.get("nodes") or [] if isinstance(item, dict)]
    impact_edges = [item for item in impact_data.get("edges") or [] if isinstance(item, dict)]
    root_entity_id = str(impact_data.get("root_entity_id") or "").strip()
    root_entity = next((item for item in repo_entities if str(item.get("id") or "").strip() == root_entity_id), None)
    if root_entity is None and repo_entities:
        root_entity = repo_entities[0]

    matched_entities = [_summarize_entity(entity) for entity in repo_entities[:6]]
    endpoint_entities = [
        _summarize_entity(entity)
        for entity in repo_entities
        if str(entity.get("entity_type") or entity.get("kind") or "").strip().lower() in {"endpoint", "route"}
    ][:4]

    impacted_files: list[str] = []
    for node in impact_nodes:
        path = str(node.get("file_path") or "").strip()
        if path and path not in impacted_files:
            impacted_files.append(path)
    adjacent_files: list[str] = []
    if root_entity is not None:
        root_file = str(root_entity.get("file_path") or "").strip()
        if root_file:
            adjacent_files.append(root_file)
    for path in impacted_files:
        if path not in adjacent_files:
            adjacent_files.append(path)
    adjacent_files = adjacent_files[:6]

    caller_entities: list[dict[str, Any]] = []
    if root_entity_id:
        inbound_ids = [
            str(edge.get("source_entity_id") or "").strip()
            for edge in impact_edges
            if str(edge.get("target_entity_id") or "").strip() == root_entity_id
        ]
        for node in impact_nodes:
            node_id = str(node.get("id") or "").strip()
            if node_id and node_id in inbound_ids:
                caller_entities.append(
                    {
                        "id": node_id,
                        "qualified_name": node.get("qualified_name") or node.get("name"),
                        "file_path": node.get("file_path"),
                        "entity_type": node.get("entity_type"),
                    }
                )
    callers = caller_entities[:4]

    test_candidates: list[dict[str, Any]] = []
    entity_needles = {
        token
        for entity in repo_entities[:6]
        for token in _query_tokens(
            " ".join(
                [
                    str(entity.get("name") or ""),
                    str(entity.get("qualified_name") or ""),
                    PurePosixPath(str(entity.get("file_path") or "unknown")).stem,
                ]
            )
        )
    }
    for path in repo_file_paths:
        normalized_path = str(path or "").replace("\\", "/").strip()
        if not normalized_path or "/tests/" not in f"/{normalized_path}":
            continue
        haystack = normalized_path.lower()
        score = 0
        for token in tokens:
            if token in haystack:
                score += 3
        for token in entity_needles:
            if token in haystack:
                score += 2
        if score <= 0:
            continue
        test_candidates.append({"file_path": normalized_path, "score": score, "provenance": "codeintel_file_index"})
    test_candidates.sort(key=lambda item: (-int(item["score"]), str(item["file_path"])))

    return {
        "primary_repo_id": primary_repo_id,
        "matched_entities": matched_entities,
        "endpoint_entities": endpoint_entities,
        "callers": callers,
        "impact_summary": {
            "root_entity_id": root_entity_id or None,
            "node_count": len(impact_nodes),
            "edge_count": len(impact_edges),
            "adjacent_files": adjacent_files,
        },
        "likely_tests": test_candidates[:4],
        "provenance": {
            "source": "codeintel_repo_scoped",
            "query": query,
            "repo_entity_count": len(repo_entities),
            "impact_direction": "inbound",
            "impact_relation_types": ["calls", "calls_resolved"],
        },
    }


def _dedupe_entities(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for group in groups:
        for entity in group:
            entity_id = str(entity.get("entity_id") or entity.get("id") or "").strip()
            key = entity_id or str(entity.get("qualified_name") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized = dict(entity)
            if entity_id and not normalized.get("id"):
                normalized["id"] = entity_id
            items.append(normalized)
    return items


@dataclass
class CodeintelQueryClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None
    haystack_adapter: HaystackRetrievalAdapter | None = None

    async def get_context(
        self,
        *,
        tenant_id: str,
        project_id: str,
        entity_query: str | None,
        retrieval_policy: dict[str, Any] | None = None,
        limit: int = 5,
    ) -> tuple[dict[str, Any], str | None]:
        if not self.service_token:
            return {}, "CODEINTEL_API_TOKEN_MISSING"
        if not project_id or project_id == "unknown":
            return {}, "CODEINTEL_PROJECT_ID_REQUIRED"
        headers = {"X-Project-ID": project_id, **_codeintel_transport_headers(self.base_url)}
        headers["Authorization"] = f"Bearer {self.service_token}"
        started = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                jobs_resp = await client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/index-jobs",
                    params={"limit": max(1, min(limit, 20)), "offset": 0},
                    headers=headers,
                )
                jobs_resp.raise_for_status()
                jobs_data = jobs_resp.json()
                jobs = jobs_data.get("data", [])
                index_job_ids = [job.get("id") for job in jobs if isinstance(job, dict) and job.get("id")]
                repos_resp = await client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/repos",
                    params={"limit": max(1, min(limit, 20)), "offset": 0},
                    headers=headers,
                )
                repos_resp.raise_for_status()
                repos_data = repos_resp.json()
                repos = repos_data.get("data", [])
                repo_ids = [repo.get("id") for repo in repos if isinstance(repo, dict) and repo.get("id")]
                requested_repo_id = ""
                requested_snapshot_id = ""
                requested_task_number: int | None = None
                requested_task_project_id = ""
                if isinstance(retrieval_policy, dict):
                    requested_repo_id = str(retrieval_policy.get("repo_id") or "").strip()
                    requested_snapshot_id = str(retrieval_policy.get("snapshot_id") or "").strip()
                    raw_task_number = retrieval_policy.get("task_number")
                    try:
                        normalized_task_number = int(str(raw_task_number or "0"))
                    except (TypeError, ValueError):
                        normalized_task_number = 0
                    if normalized_task_number > 0:
                        requested_task_number = normalized_task_number
                        requested_task_project_id = (
                            str(retrieval_policy.get("task_project_id") or "").strip() or project_id
                        )
                context_pack_repo_id = requested_repo_id or next(
                    (str(repo_id).strip() for repo_id in repo_ids if str(repo_id).strip()),
                    "",
                )
                if context_pack_repo_id and entity_query:
                    context_pack_params: dict[str, Any] = {
                        "repo_id": context_pack_repo_id,
                        "query": entity_query,
                        "limit": max(1, min(limit, 20)),
                    }
                    if requested_snapshot_id:
                        context_pack_params["snapshot_id"] = requested_snapshot_id
                    if requested_task_number is not None and requested_task_project_id:
                        context_pack_params["task_number"] = requested_task_number
                        context_pack_params["task_project_id"] = requested_task_project_id
                    try:
                        context_pack_resp = await client.get(
                            f"{self.base_url.rstrip('/')}/api/v1/answers/context-pack",
                            params=context_pack_params,
                            headers=headers,
                        )
                        context_pack_resp.raise_for_status()
                        context_pack_data = dict(context_pack_resp.json().get("data") or {})
                        context_pack_resolution = _context_pack_resolution(context_pack_data)
                        bounded_refusal_policy = _context_pack_bounded_refusal_policy(context_pack_data)
                        context_pack_snapshot_id = str(context_pack_data.get("snapshot_id") or "").strip() or None
                        context_pack_commit_sha = str(context_pack_data.get("commit_hash") or "").strip() or None
                        context_pack_repo_id = str(context_pack_data.get("repo_id") or context_pack_repo_id).strip()
                        if bounded_refusal_policy is not None:
                            context_payload: dict[str, Any] = {
                                "source": "hyeve-codeintel-service",
                                "tenant_id": tenant_id,
                                "project_id": project_id,
                                "retrieval_mode": "deterministic_context_pack",
                                "query_identity": _query_identity(
                                    project_id=project_id,
                                    entity_query=entity_query,
                                    retrieval_mode="deterministic_context_pack",
                                ),
                                "latency_ms": round((perf_counter() - started) * 1000, 2),
                                "index_jobs": jobs,
                                "index_jobs_total": jobs_data.get("pagination", {}).get("total", 0),
                                "index_job_ids": index_job_ids,
                                "repos": repos,
                                "repos_total": repos_data.get("pagination", {}).get("total", 0),
                                "repo_ids": repo_ids,
                                "entities": [],
                                "entities_total": 0,
                                "entity_ids": [],
                                "primary_repo_id": context_pack_repo_id or None,
                                "repo_entities": [],
                                "repo_entities_total": 0,
                                "repo_relations": [],
                                "repo_relations_total": 0,
                                "impact": {"root_entity_id": None, "nodes": [], "edges": []},
                                "citations": [],
                                "confidence_score": 0.0,
                                "fallback_policy": bounded_refusal_policy,
                                "grounding": {
                                    "primary_repo_id": context_pack_repo_id or None,
                                    "matched_entities": [],
                                    "endpoint_entities": [],
                                    "callers": [],
                                    "impact_summary": {
                                        "root_entity_id": None,
                                        "node_count": 0,
                                        "edge_count": 0,
                                        "adjacent_files": [],
                                    },
                                    "likely_tests": [],
                                    "provenance": {
                                        "source": "codeintel_context_pack",
                                        "query": entity_query,
                                        "repo_entity_count": 0,
                                        "impact_direction": "bounded_refusal",
                                        "impact_relation_types": [],
                                    },
                                },
                                "context_pack": context_pack_data,
                                "resolution": context_pack_resolution,
                            }
                            if retrieval_policy:
                                context_payload["retrieval_policy"] = retrieval_policy
                            if entity_query:
                                context_payload["entity_query"] = entity_query
                            return context_payload, None
                        primary_entity = (
                            _hydrate_context_pack_entity(
                                dict(context_pack_data.get("primary_entity") or {}),
                                repo_id=context_pack_repo_id or None,
                                snapshot_id=context_pack_snapshot_id,
                                commit_sha=context_pack_commit_sha,
                            )
                            if isinstance(context_pack_data.get("primary_entity"), dict)
                            else {}
                        )
                        matched_entities = [
                            _hydrate_context_pack_entity(
                                item,
                                repo_id=context_pack_repo_id or None,
                                snapshot_id=context_pack_snapshot_id,
                                commit_sha=context_pack_commit_sha,
                            )
                            for item in context_pack_data.get("matched_entities") or []
                            if isinstance(item, dict)
                        ]
                        caller_entities = [
                            _hydrate_context_pack_entity(
                                item,
                                repo_id=context_pack_repo_id or None,
                                snapshot_id=context_pack_snapshot_id,
                                commit_sha=context_pack_commit_sha,
                            )
                            for item in context_pack_data.get("callers") or []
                            if isinstance(item, dict)
                        ]
                        repo_entities = _dedupe_entities([primary_entity] if primary_entity else [], matched_entities)
                        if repo_entities or len(repo_ids) <= 1:
                            citations: list[dict[str, Any]] = []
                            for citation in context_pack_data.get("citations") or []:
                                if not isinstance(citation, dict):
                                    continue
                                evidence_payload = citation.get("evidence_payload")
                                if not isinstance(evidence_payload, dict):
                                    evidence_payload = {}
                                start_line = citation.get("citation_start_line")
                                end_line = citation.get("citation_end_line")
                                if end_line is None:
                                    end_line = start_line
                                confidence = evidence_payload.get("confidence_score")
                                if not isinstance(confidence, (int, float)):
                                    confidence = _entity_confidence(primary_entity or {}, entity_query)
                                citations.append(
                                    {
                                        "repo_id": context_pack_data.get("repo_id") or context_pack_repo_id,
                                        "file": citation.get("cited_file_path"),
                                        "line": start_line,
                                        "snapshot": citation.get("snapshot_id"),
                                        "file_path": citation.get("cited_file_path"),
                                        "start_line": start_line,
                                        "end_line": end_line,
                                        "snapshot_id": citation.get("snapshot_id"),
                                        "commit_sha": citation.get("commit_hash"),
                                        "confidence": float(confidence),
                                    }
                                )
                            if not citations:
                                citations.append(
                                    {
                                        "repo_id": context_pack_data.get("repo_id") or context_pack_repo_id,
                                        "file": None,
                                        "line": None,
                                        "snapshot": context_pack_data.get("snapshot_id"),
                                        "file_path": None,
                                        "start_line": None,
                                        "end_line": None,
                                        "snapshot_id": context_pack_data.get("snapshot_id"),
                                        "commit_sha": context_pack_data.get("commit_hash"),
                                        "confidence": 0.35,
                                    }
                                )
                            confidence_score = max((float(c.get("confidence", 0.0)) for c in citations), default=0.0)
                            fallback_policy: dict[str, Any] | None = None
                            if confidence_score < 0.6:
                                fallback_policy = {
                                    "mode": "fallback",
                                    "reason": "codeintel_low_confidence",
                                    "threshold": 0.6,
                                    "observed_confidence": confidence_score,
                                }
                            adjacent_files = [
                                str(path).strip()
                                for path in (context_pack_data.get("adjacent_files") or [])
                                if str(path).strip()
                            ]
                            likely_tests = [
                                {
                                    "file_path": _normalize_likely_test_path(path),
                                    "score": 1,
                                    "provenance": "context_pack",
                                }
                                for path in (context_pack_data.get("likely_tests") or [])
                                if _normalize_likely_test_path(path)
                            ]
                            impact_nodes = []
                            for entity in _dedupe_entities(repo_entities, caller_entities):
                                impact_nodes.append(
                                    {
                                        "id": entity.get("entity_id") or entity.get("id"),
                                        "repo_id": entity.get("provenance", {}).get("repo_id")
                                        if isinstance(entity.get("provenance"), dict)
                                        else context_pack_repo_id,
                                        "file_path": entity.get("file_path"),
                                        "entity_type": entity.get("entity_type"),
                                        "name": entity.get("name"),
                                        "qualified_name": entity.get("qualified_name"),
                                    }
                                )
                            impact = {
                                "root_entity_id": primary_entity.get("entity_id") or primary_entity.get("id"),
                                "nodes": impact_nodes,
                                "edges": [],
                            }
                            grounding = {
                                "primary_repo_id": context_pack_data.get("repo_id") or context_pack_repo_id,
                                "matched_entities": [
                                    _summarize_entity(
                                        {
                                            "id": entity.get("entity_id") or entity.get("id"),
                                            "repo_id": (
                                                entity.get("provenance", {}).get("repo_id")
                                                if isinstance(entity.get("provenance"), dict)
                                                else context_pack_repo_id
                                            ),
                                            "file_path": entity.get("file_path"),
                                            "snapshot_id": entity.get("snapshot_id") or context_pack_snapshot_id,
                                            "commit_sha": entity.get("commit_sha") or context_pack_commit_sha,
                                            "entity_type": entity.get("entity_type"),
                                            "name": entity.get("name"),
                                            "qualified_name": entity.get("qualified_name"),
                                            "signature": entity.get("signature"),
                                            "start_line": entity.get("start_line"),
                                            "end_line": entity.get("end_line"),
                                            "confidence_score": entity.get("confidence_score"),
                                            "freshness_status": entity.get("freshness_status"),
                                            "warnings": entity.get("warnings") or [],
                                            "why_ranked": entity.get("why_ranked") or [],
                                        }
                                    )
                                    for entity in repo_entities[:6]
                                ],
                                "endpoint_entities": [
                                    _summarize_entity(
                                        {
                                            "id": entity.get("entity_id") or entity.get("id"),
                                            "repo_id": context_pack_repo_id,
                                            "file_path": entity.get("file_path"),
                                            "snapshot_id": entity.get("snapshot_id") or context_pack_snapshot_id,
                                            "commit_sha": entity.get("commit_sha") or context_pack_commit_sha,
                                            "entity_type": entity.get("entity_type"),
                                            "name": entity.get("name"),
                                            "qualified_name": entity.get("qualified_name"),
                                            "signature": entity.get("signature"),
                                            "start_line": entity.get("start_line"),
                                            "end_line": entity.get("end_line"),
                                            "confidence_score": entity.get("confidence_score"),
                                            "freshness_status": entity.get("freshness_status"),
                                            "warnings": entity.get("warnings") or [],
                                            "why_ranked": entity.get("why_ranked") or [],
                                        }
                                    )
                                    for entity in repo_entities
                                    if str(entity.get("entity_type") or "").strip().lower() in {"endpoint", "route"}
                                ][:4],
                                "callers": [
                                    {
                                        "id": entity.get("entity_id") or entity.get("id"),
                                        "qualified_name": entity.get("qualified_name") or entity.get("name"),
                                        "file_path": entity.get("file_path"),
                                        "entity_type": entity.get("entity_type"),
                                    }
                                    for entity in caller_entities[:4]
                                ],
                                "impact_summary": {
                                    "root_entity_id": primary_entity.get("entity_id")
                                    or primary_entity.get("id")
                                    or None,
                                    "node_count": len(impact_nodes),
                                    "edge_count": 0,
                                    "adjacent_files": adjacent_files[:6],
                                },
                                "likely_tests": likely_tests[:4],
                                "provenance": {
                                    "source": "codeintel_context_pack",
                                    "query": entity_query,
                                    "repo_entity_count": len(repo_entities),
                                    "impact_direction": "bounded",
                                    "impact_relation_types": [],
                                },
                            }
                            context_payload = {
                                "source": "hyeve-codeintel-service",
                                "tenant_id": tenant_id,
                                "project_id": project_id,
                                "retrieval_mode": "deterministic_context_pack",
                                "query_identity": _query_identity(
                                    project_id=project_id,
                                    entity_query=entity_query,
                                    retrieval_mode="deterministic_context_pack",
                                ),
                                "latency_ms": round((perf_counter() - started) * 1000, 2),
                                "index_jobs": jobs,
                                "index_jobs_total": jobs_data.get("pagination", {}).get("total", 0),
                                "index_job_ids": index_job_ids,
                                "repos": repos,
                                "repos_total": repos_data.get("pagination", {}).get("total", 0),
                                "repo_ids": repo_ids,
                                "entities": repo_entities,
                                "entities_total": len(repo_entities),
                                "entity_ids": [
                                    entity.get("entity_id") or entity.get("id")
                                    for entity in repo_entities
                                    if entity.get("entity_id") or entity.get("id")
                                ],
                                "primary_repo_id": context_pack_data.get("repo_id") or context_pack_repo_id,
                                "repo_entities": repo_entities,
                                "repo_entities_total": len(repo_entities),
                                "repo_relations": [],
                                "repo_relations_total": 0,
                                "impact": impact,
                                "citations": citations,
                                "confidence_score": confidence_score,
                                "fallback_policy": fallback_policy,
                                "grounding": grounding,
                                "context_pack": context_pack_data,
                                "resolution": context_pack_resolution,
                            }
                            adapter_requested = False
                            if isinstance(retrieval_policy, dict):
                                adapter_requested = (
                                    str(retrieval_policy.get("adapter") or "").strip().lower() == "haystack"
                                )
                            if settings.HAYSTACK_RETRIEVAL_ENABLED or adapter_requested:
                                adapter = self.haystack_adapter or HaystackRetrievalAdapter(
                                    top_k=settings.HAYSTACK_RETRIEVAL_TOP_K
                                )
                                ranking = adapter.rank_context_chunks(
                                    query=entity_query,
                                    entities=repo_entities if isinstance(repo_entities, list) else [],
                                    citations=citations,
                                )
                                context_payload["retrieval_adapter"] = ranking.get("adapter")
                                context_payload["retrieval_ms"] = ranking.get("retrieval_ms")
                                context_payload["corpus_hits"] = ranking.get("corpus_hits")
                                context_payload["topk_returned"] = ranking.get("topk_returned")
                                context_payload["ranked_chunks"] = ranking.get("ranked_chunks")
                                context_payload["source_refs"] = ranking.get("source_refs")
                            if retrieval_policy:
                                context_payload["retrieval_policy"] = retrieval_policy
                            if entity_query:
                                context_payload["entity_query"] = entity_query
                            return context_payload, None
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code not in {404, 405, 501}:
                            raise
                file_lookup: dict[str, dict[str, Any]] = {}
                repo_file_paths: dict[str, list[str]] = {}
                for repo_id in repo_ids:
                    files_resp = await client.get(
                        f"{self.base_url.rstrip('/')}/api/v1/repos/{repo_id}/files",
                        params={"limit": max(1, min(limit * 10, 200)), "offset": 0},
                        headers=headers,
                    )
                    files_resp.raise_for_status()
                    files_data = files_resp.json()
                    repo_paths: list[str] = []
                    for file_item in files_data.get("data", []):
                        if not isinstance(file_item, dict):
                            continue
                        file_id = file_item.get("id")
                        if not file_id:
                            continue
                        file_lookup[str(file_id)] = file_item
                        file_path = str(file_item.get("file_path") or "").strip()
                        if file_path:
                            repo_paths.append(file_path)
                    repo_file_paths[str(repo_id)] = repo_paths
                entities_params: dict[str, Any] = {"limit": max(1, min(limit, 20)), "offset": 0}
                if entity_query:
                    entities_params["query"] = entity_query
                entities_resp = await client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/entities",
                    params=entities_params,
                    headers=headers,
                )
                entities_resp.raise_for_status()
                entities_data = entities_resp.json()
                entities = entities_data.get("data", [])
                entity_ids = [entity.get("id") for entity in entities if isinstance(entity, dict) and entity.get("id")]
                primary_repo_id = next(
                    (
                        str(entity.get("repo_id") or "").strip()
                        for entity in entities
                        if str(entity.get("repo_id") or "").strip()
                    ),
                    "",
                ) or next((str(repo_id).strip() for repo_id in repo_ids if str(repo_id).strip()), "")
                legacy_repo_entities: list[dict[str, Any]] = []
                repo_relations: list[dict[str, Any]] = []
                impact_payload: dict[str, Any] | None = None
                if primary_repo_id:
                    repo_entities_params: dict[str, Any] = {"limit": max(1, min(limit * 2, 12)), "offset": 0}
                    if entity_query:
                        repo_entities_params["query"] = entity_query
                    repo_entities_resp = await client.get(
                        f"{self.base_url.rstrip('/')}/api/v1/repos/{primary_repo_id}/entities",
                        params=repo_entities_params,
                        headers=headers,
                    )
                    repo_entities_resp.raise_for_status()
                    legacy_repo_entities = _extract_items(repo_entities_resp.json())

                    repo_relations_resp = await client.get(
                        f"{self.base_url.rstrip('/')}/api/v1/repos/{primary_repo_id}/relations",
                        params={"limit": max(12, min(limit * 8, 48)), "offset": 0},
                        headers=headers,
                    )
                    repo_relations_resp.raise_for_status()
                    repo_relations = _extract_items(repo_relations_resp.json())

                    primary_entity_id = next(
                        (
                            str(entity.get("id") or "").strip()
                            for entity in legacy_repo_entities
                            if str(entity.get("id") or "").strip()
                        ),
                        "",
                    )
                    if primary_entity_id:
                        impact_resp = await client.get(
                            f"{self.base_url.rstrip('/')}/api/v1/entities/{primary_entity_id}/impact",
                            params={
                                "max_depth": 2,
                                "direction": "inbound",
                                "relation_types": "calls,calls_resolved",
                            },
                            headers=headers,
                        )
                        impact_resp.raise_for_status()
                        impact_payload = impact_resp.json()
                legacy_citations: list[dict[str, Any]] = []
                for entity in entities:
                    if not isinstance(entity, dict):
                        continue
                    file_id = str(entity.get("file_id") or "")
                    file_row = file_lookup.get(file_id, {})
                    start_line = entity.get("start_line")
                    end_line = entity.get("end_line")
                    if end_line is None:
                        end_line = start_line
                    citation_file_path = file_row.get("file_path")
                    snapshot_id = entity.get("snapshot_id") or file_row.get("snapshot_id")
                    commit_sha = entity.get("commit_sha") or file_row.get("commit_sha")
                    legacy_citations.append(
                        {
                            "repo_id": entity.get("repo_id"),
                            "file": str(citation_file_path) if citation_file_path is not None else None,
                            "line": start_line,
                            "snapshot": snapshot_id,
                            "file_path": str(citation_file_path) if citation_file_path is not None else None,
                            "start_line": start_line,
                            "end_line": end_line,
                            "snapshot_id": snapshot_id,
                            "commit_sha": commit_sha,
                            "confidence": _entity_confidence(entity, entity_query),
                        }
                    )
                if not legacy_citations:
                    for repo_id in repo_ids:
                        legacy_citations.append(
                            {
                                "repo_id": repo_id,
                                "file": None,
                                "line": None,
                                "snapshot": None,
                                "file_path": None,
                                "start_line": None,
                                "end_line": None,
                                "snapshot_id": None,
                                "commit_sha": None,
                                "confidence": 0.35,
                            }
                        )
                confidence_score = max((float(c.get("confidence", 0.0)) for c in legacy_citations), default=0.0)
                legacy_fallback_policy: dict[str, Any] | None = None
                if confidence_score < 0.6:
                    legacy_fallback_policy = {
                        "mode": "fallback",
                        "reason": "codeintel_low_confidence",
                        "threshold": 0.6,
                        "observed_confidence": confidence_score,
                    }
                grounding = _build_grounding_bundle(
                    query=entity_query,
                    primary_repo_id=primary_repo_id or None,
                    repo_entities=legacy_repo_entities,
                    repo_file_paths=repo_file_paths.get(primary_repo_id, []),
                    impact_payload=impact_payload,
                )
                legacy_context: dict[str, Any] = {
                    "source": "hyeve-codeintel-service",
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "retrieval_mode": "codeintel_first",
                    "query_identity": _query_identity(
                        project_id=project_id,
                        entity_query=entity_query,
                        retrieval_mode="codeintel_first",
                    ),
                    "latency_ms": round((perf_counter() - started) * 1000, 2),
                    "index_jobs": jobs,
                    "index_jobs_total": jobs_data.get("pagination", {}).get("total", 0),
                    "index_job_ids": index_job_ids,
                    "repos": repos,
                    "repos_total": repos_data.get("pagination", {}).get("total", 0),
                    "repo_ids": repo_ids,
                    "entities": entities,
                    "entities_total": entities_data.get("pagination", {}).get("total", 0),
                    "entity_ids": entity_ids,
                    "primary_repo_id": primary_repo_id or None,
                    "repo_entities": legacy_repo_entities,
                    "repo_entities_total": len(legacy_repo_entities),
                    "repo_relations": repo_relations,
                    "repo_relations_total": len(repo_relations),
                    "impact": dict((impact_payload or {}).get("data") or {}),
                    "citations": legacy_citations,
                    "confidence_score": confidence_score,
                    "fallback_policy": legacy_fallback_policy,
                    "grounding": grounding,
                }
                adapter_requested = False
                if isinstance(retrieval_policy, dict):
                    adapter_requested = str(retrieval_policy.get("adapter") or "").strip().lower() == "haystack"
                if settings.HAYSTACK_RETRIEVAL_ENABLED or adapter_requested:
                    adapter = self.haystack_adapter or HaystackRetrievalAdapter(top_k=settings.HAYSTACK_RETRIEVAL_TOP_K)
                    ranking = adapter.rank_context_chunks(
                        query=entity_query,
                        entities=entities if isinstance(entities, list) else [],
                        citations=legacy_citations,
                    )
                    legacy_context["retrieval_adapter"] = ranking.get("adapter")
                    legacy_context["retrieval_ms"] = ranking.get("retrieval_ms")
                    legacy_context["corpus_hits"] = ranking.get("corpus_hits")
                    legacy_context["topk_returned"] = ranking.get("topk_returned")
                    legacy_context["ranked_chunks"] = ranking.get("ranked_chunks")
                    legacy_context["source_refs"] = ranking.get("source_refs")
                if retrieval_policy:
                    legacy_context["retrieval_policy"] = retrieval_policy
                if entity_query:
                    legacy_context["entity_query"] = entity_query
                return legacy_context, None
        except Exception as exc:  # pragma: no cover
            return {}, str(exc)


codeintel_query_client = CodeintelQueryClient(
    base_url=str(settings.CODEINTEL_API_URL),
    timeout_seconds=settings.CODEINTEL_API_TIMEOUT_SECONDS,
    service_token=settings.CODEINTEL_API_TOKEN,
    haystack_adapter=HaystackRetrievalAdapter(top_k=settings.HAYSTACK_RETRIEVAL_TOP_K),
)
