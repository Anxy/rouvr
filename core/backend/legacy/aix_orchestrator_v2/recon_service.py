from __future__ import annotations

import ast
import shlex
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.legacy.aix_orchestrator_v2.discipline import resolve_canonical_contract, validate_recon_admissibility
from backend.aix_orchestrator_v2.models import ReconResult
from backend.clients.codeintel_query_client import codeintel_query_client
from backend.services.devexecutor_python_symbol_service import resolve_python_symbol_range


def _codeintel_posture() -> str:
    """Wave 0 authority freeze: off = no calls; advisory = calls allowed, results never targets."""
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return str(getattr(_settings, "AIX_CODEINTEL", "off") or "off").strip().lower()
    except Exception:
        return "off"


def _workspace_head_sha(*, workspace_root: str | None) -> str | None:
    """Wave 0 freshness: workspace HEAD for anchor commit comparison. None when unresolvable."""
    try:
        import subprocess

        root = str(workspace_root or "").strip()
        if not root:
            return None
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = str(proc.stdout or "").strip()
        return sha or None
    except Exception:
        return None


def _anchor_freshness(*, commit_sha: str, head_sha: str | None) -> str:
    """Wave 0: unknown when unresolvable; current only on HEAD match; else stale."""
    commit = str(commit_sha or "").strip()
    if not commit or commit == "unknown":
        return "unknown"
    if head_sha and (head_sha.startswith(commit) or commit.startswith(head_sha)):
        return "current"
    return "stale"


def _contract_target_authorized(*, target_file: str, workspace_root: str | None) -> bool:
    """Wave 4b: contract names a target AND it exists readable on disk."""
    text = str(target_file or "").strip()
    if not text:
        return False
    resolved = _resolve_workspace_target_file(
        workspace_root=Path(str(workspace_root or "").strip() or "."),
        target_file=text,
    )
    if resolved is None:
        return False
    try:
        with open(Path(str(workspace_root or "").strip() or ".") / resolved, "rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def _classify_retrieval_error(message: str | None) -> str:
    """Wave 4b: classified CI failure for degraded evidence (never a traceback)."""
    text = str(message or "").strip().lower()
    if not text:
        return "unknown"
    if "timed out" in text or "timeout" in text or "deadline" in text:
        return "timeout"
    if "refused" in text or "unreachable" in text or "failed to connect" in text:
        return "connection"
    if "resolution" in text or "dns" in text or "name or service" in text:
        return "dns"
    if "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return "auth"
    if "429" in text or "rate" in text:
        return "rate_limited"
    if "500" in text or "502" in text or "503" in text or "504" in text:
        return "server"
    return "unknown"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_int(value: Any) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _extract_symbol_name(value: Any) -> str:
    if isinstance(value, dict):
        for candidate in (value.get("symbol"), value.get("qualified_name"), value.get("name")):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("{") and text.endswith("}"):
        return ""
    return text


def _tokenize_symbol_hint(value: str) -> set[str]:
    tokens = {
        item.strip().lower()
        for item in str(value or "").replace("-", "_").replace("/", "_").replace(".", "_").split("_")
        if item.strip()
    }
    return {item for item in tokens if len(item) >= 3}


def _infer_python_target_symbol(
    *,
    workspace_root: str | None,
    target_file: str,
    objective: str,
) -> str:
    workspace_text = str(workspace_root or "").strip()
    target_text = str(target_file or "").strip()
    if not workspace_text or not target_text or not target_text.endswith(".py"):
        return ""
    workspace_path = Path(workspace_text)
    resolved_target = _resolve_workspace_target_file(workspace_root=workspace_path, target_file=target_text)
    if not resolved_target:
        return ""
    file_path = workspace_path / resolved_target
    try:
        module = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return ""
    hint_tokens = _tokenize_symbol_hint(Path(target_text).stem)
    hint_tokens.update(_tokenize_symbol_hint(objective))
    best_name = ""
    best_score = -1
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        symbol_name = str(getattr(node, "name", "") or "").strip()
        if not symbol_name:
            continue
        score = 0
        symbol_tokens = _tokenize_symbol_hint(symbol_name)
        overlap = hint_tokens.intersection(symbol_tokens)
        score += len(overlap) * 10
        if hint_tokens and any(token in symbol_name.lower() for token in hint_tokens):
            score += 5
        if symbol_name.startswith("build_"):
            score += 2
        if isinstance(node, ast.ClassDef):
            score -= 1
        if score > best_score:
            best_score = score
            best_name = symbol_name
    return best_name if best_score > 0 else ""


def _bounded_grounded_file_slice(
    *, workspace_root: str | None, target_file: str, target_symbol: str | None = None
) -> dict[str, Any]:
    workspace_text = str(workspace_root or "").strip()
    target_text = str(target_file or "").strip()
    if not workspace_text or not target_text:
        return {}
    workspace_path = Path(workspace_text)
    resolved_target = _resolve_workspace_target_file(
        workspace_root=workspace_path,
        target_file=target_text,
    )
    if not resolved_target:
        return {}
    file_path = workspace_path / resolved_target
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    symbol_name = str(target_symbol or "").strip()
    if symbol_name and target_text.endswith(".py"):
        try:
            symbol_range = resolve_python_symbol_range(
                file_path=file_path,
                symbol_name=symbol_name.rsplit(".", 1)[-1],
            )
            symbol_source = str(symbol_range.symbol_source or "").replace("\r\n", "\n").replace("\r", "\n")
            if symbol_source:
                return {
                    "target_file": resolved_target,
                    "output_content": symbol_source,
                    "start_line": int(symbol_range.start_line),
                    "end_line": int(symbol_range.end_line),
                }
        except Exception:
            pass
    line_count = len(normalized.splitlines()) if normalized else 0
    if line_count > 200 or len(normalized.encode("utf-8")) > 24_000:
        return {}
    return {
        "target_file": resolved_target,
        "output_content": normalized,
        "start_line": 1,
        "end_line": max(1, line_count),
    }


def _resolve_workspace_target_file(*, workspace_root: Path, target_file: str) -> str | None:
    target_text = str(target_file or "").strip().replace("\\", "/")
    if not target_text:
        return None
    direct = workspace_root / target_text
    if direct.is_file():
        return target_text
    target_name = Path(target_text).name
    matches: list[str] = []
    try:
        for candidate in workspace_root.rglob(target_name):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(workspace_root).as_posix()
            if relative == target_text or relative.endswith(f"/{target_text}"):
                matches.append(relative)
    except OSError:
        return None
    if not matches:
        return None
    matches = sorted(set(matches), key=lambda item: (item.count("/"), len(item)))
    return matches[0]


def _canonical_path_text(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return "/".join(part for part in text.strip("/").split("/") if part)


def _canonical_workspace_target(*, workspace_root: str | None, target_file: Any) -> str:
    normalized = _canonical_path_text(target_file)
    if not normalized:
        return ""
    workspace_text = str(workspace_root or "").strip()
    if not workspace_text:
        return normalized
    resolved = _resolve_workspace_target_file(workspace_root=Path(workspace_text), target_file=normalized)
    return str(resolved or normalized).strip().replace("\\", "/").strip("/")


def _canonicalize_contract_target_paths(
    *,
    contract: dict[str, Any],
    workspace_root: str | None,
    resolved_target_file: str | None,
) -> dict[str, Any]:
    canonical = _canonical_path_text(resolved_target_file)
    updated = dict(contract)
    target = dict(_as_dict(updated.get("target")))
    target_surface = dict(_as_dict(updated.get("target_surface")))
    execution_slice = dict(_as_dict(updated.get("execution_slice")))

    def _canonicalize(value: Any) -> str:
        return canonical or _canonical_workspace_target(workspace_root=workspace_root, target_file=value)

    if str(updated.get("target_file") or "").strip() or canonical:
        updated["target_file"] = _canonicalize(updated.get("target_file"))
    if str(target.get("file_path") or "").strip() or canonical:
        target["file_path"] = _canonicalize(target.get("file_path"))
        updated["target"] = target
    if str(execution_slice.get("target_file") or "").strip() or canonical:
        execution_slice["target_file"] = _canonicalize(execution_slice.get("target_file"))

    target_files = [
        _canonicalize(item)
        for item in list(target_surface.get("target_files") or execution_slice.get("target_files") or [])
        if _canonicalize(item)
    ]
    if canonical and canonical not in target_files:
        target_files.insert(0, canonical)
    if target_files:
        canonical_files = list(dict.fromkeys(target_files))
        target_surface["target_files"] = canonical_files
        execution_slice["target_files"] = canonical_files
    if target_surface:
        updated["target_surface"] = target_surface
    if execution_slice:
        updated["execution_slice"] = execution_slice
    return updated


def _pick_execution_contract(task_payload: dict[str, Any], execution_contracts: list[dict[str, Any]]) -> dict[str, Any]:
    outcome = resolve_canonical_contract(task_payload=task_payload, execution_contracts=execution_contracts)
    if not outcome.ok:
        return {"_contract_error": outcome.reason_code, "_contract_error_payload": outcome.payload or {}}
    payload = outcome.payload or {}
    return _as_dict(payload.get("contract"))


def _contract_target_file(contract: dict[str, Any]) -> str:
    target_surface = _as_dict(contract.get("target_surface"))
    execution_slice = _as_dict(contract.get("execution_slice"))
    for candidate in (
        contract.get("target_file"),
        _as_dict(contract.get("target")).get("file_path"),
        execution_slice.get("target_file"),
        next(iter(target_surface.get("target_files") or []), None),
        next(iter(execution_slice.get("target_files") or []), None),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _contract_target_symbol(contract: dict[str, Any]) -> str:
    target_surface = _as_dict(contract.get("target_surface"))
    for candidate in (
        contract.get("target_symbol"),
        _as_dict(contract.get("target")).get("symbol"),
        next(iter(target_surface.get("owning_symbols") or []), None),
        next(iter(target_surface.get("prompt_symbol_targets") or []), None),
    ):
        text = _extract_symbol_name(candidate)
        if text:
            return text
    return ""


def _extract_validation_command(contract: dict[str, Any], task_payload: dict[str, Any]) -> str:
    execution_slice = _as_dict(contract.get("execution_slice"))
    for collection in (execution_slice.get("validation_commands"),):
        items = [str(item or "").strip() for item in list(collection or []) if str(item or "").strip()]
        if items:
            return items[0]
    if contract:
        return "pytest"
    metadata = _as_dict(task_payload.get("metadata_json"))
    for collection in (metadata.get("validation_commands"),):
        items = [str(item or "").strip() for item in list(collection or []) if str(item or "").strip()]
        if items:
            return items[0]
    for candidate in (metadata.get("validation_command"),):
        text = str(candidate or "").strip()
        if text:
            return text
    return "pytest"


def _extract_target_file_from_validation_command(command_text: str) -> str:
    text = str(command_text or "").strip()
    if not text:
        return ""
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return ""
    for token in tokens:
        token_text = str(token or "").strip().replace("\\", "/")
        if not token_text or ".py" not in token_text:
            continue
        if ".py::" in token_text:
            return token_text.split(".py::", 1)[0] + ".py"
        if token_text.endswith(".py"):
            return token_text
    return ""


def _normalize_validation_token_for_workspace(
    *,
    token: str,
    workspace_root: str | None,
    primary_target_file: str | None,
    target_files: list[str] | None,
) -> str:
    token_text = str(token or "").strip()
    if not token_text or ("/" not in token_text and "\\" not in token_text and ".py" not in token_text):
        return token_text
    selector = ""
    base_token = token_text.replace("\\", "/")
    if ".py::" in base_token:
        prefix, suffix = base_token.split(".py::", 1)
        base_token = f"{prefix}.py"
        selector = f"::{suffix}"
    workspace_text = str(workspace_root or "").strip()
    if not workspace_text:
        return token_text
    workspace_path = Path(workspace_text)
    resolved = _resolve_workspace_target_file(workspace_root=workspace_path, target_file=base_token)
    if not resolved:
        candidate_paths: list[str] = []
        for candidate in [primary_target_file, *(target_files or [])]:
            resolved_candidate = _resolve_workspace_target_file(
                workspace_root=workspace_path,
                target_file=str(candidate or "").strip(),
            )
            if resolved_candidate and resolved_candidate not in candidate_paths:
                candidate_paths.append(resolved_candidate)
        for candidate in candidate_paths:
            if (
                candidate == base_token
                or candidate.endswith(f"/{base_token}")
                or Path(candidate).name == Path(base_token).name
            ):
                resolved = candidate
                break
    if not resolved:
        return token_text
    if selector:
        return f"{resolved}{selector}"
    return resolved


def _normalize_validation_command_for_workspace(
    *,
    command_text: str,
    workspace_root: str | None,
    primary_target_file: str | None,
    target_files: list[str] | None,
) -> str:
    text = str(command_text or "").strip()
    if not text:
        return text
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return text
    changed = False
    normalized_tokens: list[str] = []
    for token in tokens:
        normalized = _normalize_validation_token_for_workspace(
            token=str(token),
            workspace_root=workspace_root,
            primary_target_file=primary_target_file,
            target_files=target_files,
        )
        normalized_tokens.append(normalized)
        if normalized != token:
            changed = True
    if not changed:
        return text
    return " ".join(shlex.quote(token) for token in normalized_tokens)


def _build_anchor(citation: dict[str, Any], entity: dict[str, Any]) -> dict[str, Any]:
    file_path = str(citation.get("file_path") or entity.get("file_path") or "").strip()
    start_line = citation.get("start_line") or entity.get("start_line")
    end_line = citation.get("end_line") or entity.get("end_line") or start_line
    symbol = str(entity.get("qualified_name") or entity.get("name") or "").strip() or None
    snapshot_id = str(citation.get("snapshot_id") or entity.get("snapshot_id") or "unknown").strip()
    commit_sha = str(citation.get("commit_sha") or entity.get("commit_sha") or "unknown").strip()
    symbol_hash = sha256(
        f"{file_path}:{start_line}:{end_line}:{symbol or ''}:{snapshot_id}:{commit_sha}".encode()
    ).hexdigest()
    return {
        "file_path": file_path,
        "symbol": symbol,
        "signature": str(entity.get("signature") or "").strip() or None,
        "start_line": _coerce_int(start_line),
        "end_line": _coerce_int(end_line),
        "snapshot_id": snapshot_id,
        "commit_sha": commit_sha,
        "symbol_source_hash": symbol_hash,
        "anchor_source": "codeintel_context",
    }


async def build_recon(
    *,
    tenant_id: str,
    project_id: str,
    task_payload: dict[str, Any],
    execution_contracts: list[dict[str, Any]],
    workspace_root: str | None = None,
) -> dict[str, Any]:
    contract = _pick_execution_contract(task_payload, execution_contracts)
    contract_error = str(contract.get("_contract_error") or "").strip()
    if contract_error:
        return ReconResult(
            status="insufficient",
            reason=contract_error,
            entity_query="",
            execution_contract={},
            context_error=contract_error,
            codeintel={},
            target_file=None,
            target_symbol=None,
            target_files=[],
            anchor={},
            symbol_ranges=[],
            anchor_ranges=[],
            validation_target="",
            related_tests=[],
            dependency_context={},
            slim_packet_inputs={},
        ).model_dump(mode="python")
    objective = str(
        contract.get("objective")
        or task_payload.get("title")
        or _as_dict(task_payload.get("metadata_json")).get("objective")
        or ""
    ).strip()
    target_file = str(
        _contract_target_file(contract)
        or _as_dict(task_payload.get("metadata_json")).get("target_file")
        or _extract_target_file_from_validation_command(_extract_validation_command(contract, task_payload))
        or ""
    ).strip()
    target_symbol = str(
        _contract_target_symbol(contract) or _as_dict(task_payload.get("metadata_json")).get("target_symbol") or ""
    ).strip()
    posture = _codeintel_posture()
    if not target_symbol:
        if posture == "on":
            target_symbol = _infer_python_target_symbol(
                workspace_root=workspace_root,
                target_file=target_file,
                objective=objective,
            )
        # Wave 0 quarantine: heuristic symbol inference preserved above but
        # gated on posture "on" (rejected in v2) — missing stays missing.
    def _blocked(reason: str, *, retrieval_skipped: bool = True) -> dict[str, Any]:
        # Wave 0/Wave 1 BLOCKED: zero targets, zero anchor downstream.
        payload = ReconResult(
            status="insufficient",
            reason=reason,
            entity_query="",
            execution_contract=contract,
            context_error=None,
            codeintel={"wave0_posture": posture, "retrieval_skipped": retrieval_skipped},
            target_file=None,
            target_symbol=None,
            target_files=[],
            anchor={},
            symbol_ranges=[],
            anchor_ranges=[],
            validation_target="",
            related_tests=[],
            dependency_context={},
            slim_packet_inputs={},
        ).model_dump(mode="python")
        payload["advisory_degraded"] = True
        return payload

    if not target_file:
        # Wave 0 BLOCKED: missing contract target cannot be backfilled by
        # retrieval. Zero writes downstream: no targets, no anchor.
        return _blocked("CONTRACT_INCOMPLETE")
    if _resolve_workspace_target_file(
        workspace_root=Path(str(workspace_root or "").strip() or "."),
        target_file=target_file,
    ) is None:
        # Wave 0 BLOCKED: contract names a file the workspace does not contain.
        return _blocked("FS_MISMATCH")
    entity_query = target_symbol or target_file or objective or str(task_payload.get("description") or "").strip()
    retrieval_policy = {
        "task_number": _coerce_int(task_payload.get("task_number")),
        "task_project_id": project_id,
        "repo_id": str(contract.get("repo_id") or "").strip() or None,
        "snapshot_id": str(contract.get("snapshot_id") or "").strip() or None,
    }
    if posture == "off":
        # Wave 0: no CodeIntel/Haystack calls. context_error stays None so the
        # admissibility gate treats this as contract+workspace grounding, not
        # a CodeIntel failure demanding a CodeIntel anchor.
        context: dict[str, Any] = {"wave0_posture": posture, "retrieval_skipped": True}
        context_error = None
    else:
        context, context_error = await codeintel_query_client.get_context(
            tenant_id=tenant_id,
            project_id=project_id,
            entity_query=entity_query,
            retrieval_policy=retrieval_policy,
            limit=6,
        )
    entities = _as_items(context.get("repo_entities") or context.get("entities"))
    citations = _as_items(context.get("citations"))
    primary_entity = entities[0] if entities else {}
    primary_citation = citations[0] if citations else {}
    anchor = _build_anchor(primary_citation, primary_entity) if (primary_entity or primary_citation) else {}
    if posture != "on" and anchor:
        # Wave 0 citation schema + freshness: anchor survives only with
        # path+snapshot+commit present and non-unknown; freshness attached.
        # It still never supplies a missing target (see resolved/grounded below).
        anchor_commit = str(anchor.get("commit_sha") or "").strip()
        anchor_snapshot = str(anchor.get("snapshot_id") or "").strip()
        anchor_path = str(anchor.get("file_path") or "").strip()
        if not anchor_path or not anchor_snapshot or not anchor_commit or anchor_commit == "unknown":
            anchor = {}
        else:
            anchor["freshness"] = _anchor_freshness(
                commit_sha=anchor_commit,
                head_sha=_workspace_head_sha(workspace_root=workspace_root),
            )
    grounding = _as_dict(context.get("grounding"))
    likely_tests = [
        str(item.get("file_path") or "").strip()
        for item in _as_items(grounding.get("likely_tests"))
        if str(item.get("file_path") or "").strip()
    ]
    adjacent_files = [
        str(item).strip()
        for item in list(_as_dict(grounding.get("impact_summary")).get("adjacent_files") or [])
        if str(item).strip()
    ]
    required_target_files = list(
        dict.fromkeys(
            [
                *[
                    str(item).strip()
                    for item in (
                        _as_dict(contract.get("execution_slice")).get("target_files")
                        or _as_dict(contract.get("target_surface")).get("target_files")
                        or []
                    )
                    if str(item).strip()
                ]
            ]
        )
    )
    target_files = []
    # Wave 0: retrieval-derived candidates (anchor file, adjacent files) may
    # never join the target set. Contract files only.
    extra_candidates: list[str] = []
    if posture == "on":
        extra_candidates = [str(anchor.get("file_path") or ""), *adjacent_files]
    for candidate in [target_file, *required_target_files, *extra_candidates]:
        text = str(candidate or "").strip()
        if text and text not in target_files:
            target_files.append(text)
    # Wave 1: every file in scope must exist on disk and be readable, before
    # any grounding output or downstream model call. First missing file BLOCKS.
    _scope_root = Path(str(workspace_root or "").strip() or ".")
    for scoped in target_files:
        _resolved = _resolve_workspace_target_file(workspace_root=_scope_root, target_file=scoped)
        _readable = False
        if _resolved is not None:
            try:
                with open(_scope_root / _resolved, "rb") as _handle:
                    _handle.read(1)
                _readable = True
            except OSError:
                _readable = False
        if not _readable:
            return _blocked("FS_MISMATCH")
    resolved_target_file = target_file or (
        str(anchor.get("file_path") or "").strip() if posture == "on" else ""
    )
    grounded_symbol = target_symbol or (
        str(anchor.get("symbol") or "").strip() if posture == "on" else ""
    )
    anchor_ranges: list[dict[str, Any]] = []
    for index, candidate_file in enumerate(target_files or [resolved_target_file]):
        candidate_file = str(candidate_file or "").strip()
        if not candidate_file:
            continue
        candidate_symbol = grounded_symbol if index == 0 else None
        grounded_slice = _bounded_grounded_file_slice(
            workspace_root=workspace_root,
            target_file=candidate_file,
            target_symbol=candidate_symbol or None,
        )
        if not grounded_slice:
            continue
        candidate_anchor = {
            **anchor,
            "requested_file_path": candidate_file,
            "file_path": str(grounded_slice.get("target_file") or candidate_file),
            "output_content": grounded_slice["output_content"],
            "start_line": grounded_slice["start_line"],
            "end_line": grounded_slice["end_line"],
            "anchor_source": str(anchor.get("anchor_source") or "workspace_file_slice"),
        }
        if index == 0:
            resolved_target_file = str(candidate_anchor.get("file_path") or resolved_target_file)
            anchor = candidate_anchor
        anchor_ranges.append(candidate_anchor)
    validation_target = _normalize_validation_command_for_workspace(
        command_text=_extract_validation_command(contract, task_payload),
        workspace_root=workspace_root,
        primary_target_file=resolved_target_file,
        target_files=target_files,
    )
    canonical_target_files = [
        _canonical_path_text(item.get("file_path") or item.get("target_file"))
        for item in anchor_ranges
        if _canonical_path_text(item.get("file_path") or item.get("target_file"))
    ]
    if resolved_target_file:
        canonical_target_files.insert(0, _canonical_path_text(resolved_target_file))
    canonical_target_files = list(dict.fromkeys(item for item in canonical_target_files if item))
    canonical_contract = _canonicalize_contract_target_paths(
        contract=contract,
        workspace_root=workspace_root,
        resolved_target_file=resolved_target_file,
    )
    recon_status = "ready"
    recon_reason = None
    if not resolved_target_file:
        recon_status = "insufficient"
        recon_reason = "target_file_missing"
    recon_payload = ReconResult(
        status=recon_status,
        reason=recon_reason,
        entity_query=entity_query,
        execution_contract=canonical_contract,
        context_error=context_error,
        codeintel=context,
        target_file=resolved_target_file or None,
        target_symbol=target_symbol
        or (str(anchor.get("symbol") or "").strip() if posture == "on" else "")
        or None,
        target_files=canonical_target_files[:8],
        anchor=anchor,
        symbol_ranges=[
            {
                "qualified_name": str(item.get("qualified_name") or item.get("name") or "").strip() or None,
                "file_path": str(item.get("file_path") or "").strip() or None,
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
            }
            for item in entities[:6]
        ],
        anchor_ranges=anchor_ranges,
        validation_target=validation_target,
        related_tests=likely_tests[:4],
        dependency_context={
            "adjacent_files": adjacent_files[:6],
            "callers": list(_as_items(grounding.get("callers")))[:4],
            "matched_entities": list(_as_items(grounding.get("matched_entities")))[:6],
            "endpoint_entities": list(_as_items(grounding.get("endpoint_entities")))[:4],
        },
        slim_packet_inputs={
            "task_number": _coerce_int(task_payload.get("task_number")),
            "execution_contract_ref": str(contract.get("id") or contract.get("contract_id") or "").strip() or None,
            "validation_command": validation_target,
            "target_file": resolved_target_file or None,
            "target_symbol": target_symbol or None,
        },
    ).model_dump(mode="python")
    admissibility = validate_recon_admissibility(recon=recon_payload, canonical_contract=contract)
    if not admissibility.ok:
        recon_payload["status"] = "insufficient"
        recon_payload["reason"] = str(admissibility.reason_code or "insufficient_context")
    if (
        posture == "advisory"
        and recon_payload["status"] == "insufficient"
        and recon_payload["reason"] == "insufficient_context"
        and context_error is not None
        and _contract_target_authorized(
            target_file=target_file, workspace_root=workspace_root
        )
    ):
        # Wave 4b doctrine change: advisory + dead CI degrades, not blocks —
        # but ONLY when contract+FS already authorize the target. Missing
        # contract (CONTRACT_INCOMPLETE) and FS_MISMATCH still block above and
        # never reach here. A CI error alone can neither grant nor grow scope.
        recon_payload["status"] = "ready"
        recon_payload["reason"] = None
        recon_payload["codeintel_error_class"] = _classify_retrieval_error(context_error)
        recon_payload["freshness"] = "unknown"
        recon_payload["degraded_reason"] = (
            "codeintel_unreachable_under_advisory_posture:contract_filesystem_grounding_only"
        )
    # Wave 0 evidence flag: proceeded without full retrieval authority whenever
    # posture is not "on" and retrieval was skipped, failed, or low-confidence.
    _ci_conf = 0.0
    try:
        _ci_conf = float((context or {}).get("confidence_score") or 0.0)
    except (TypeError, ValueError):
        _ci_conf = 0.0
    recon_payload["advisory_degraded"] = bool(
        posture != "on"
        and (
            (context or {}).get("retrieval_skipped") is True
            or context_error is not None
            or _ci_conf < 0.6
        )
    )
    return recon_payload
