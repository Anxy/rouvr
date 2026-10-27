"""Pre-execution plan resolver service (AIFIRST-5 #1814).

Resolves ``symbol_name`` fields in planner steps to exact line numbers before
execution, eliminating anchor-mismatch failures.

Resolution pipeline (tried in order):
  1. CodeIntel entity lookup  — exact index-backed match
  2. AST parse fallback       — direct filesystem read + ``ast`` walk

For ``confirm_anchor`` steps the ``anchor_quote`` is verified against the
actual file text and corrected for whitespace drift.

The resolver is transparent post-processing: no change to the planner prompt
is required.  Diagnostics are returned so the router can log them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.services.devexecutor_python_symbol_service import resolve_python_symbol_range

# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------
SYMBOL_EDIT_TOOLS: frozenset[str] = frozenset({"replace_in_function", "update_call_kwarg"})


class CodeIntelGroundingFailure(RuntimeError):
    """Raised when required CodeIntel grounding is unavailable."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_symbol(symbol_name: str, file_path: Path) -> dict[str, Any] | None:
    if not file_path.exists():
        return None
    try:
        symbol = resolve_python_symbol_range(file_path=file_path, symbol_name=symbol_name)
    except (LookupError, OSError, SyntaxError, ValueError):
        return None
    source_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    signature = source_lines[symbol.def_line - 1].rstrip() if symbol.def_line <= len(source_lines) else ""
    return {
        "start_line": symbol.start_line,
        "end_line": symbol.end_line,
        "signature": signature,
        "source": symbol.resolution_source,
    }


def _ast_corrected_anchor(anchor_quote: str, file_path: Path) -> str | None:
    """Find the line in *file_path* that best matches *anchor_quote*.

    Returns the exact line from the file (no trailing whitespace) if found,
    or ``None`` if no match.
    """
    if not file_path.exists():
        return None
    try:
        source_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    needle = anchor_quote.strip()
    for line in source_lines:
        stripped = line.strip()
        if stripped == needle:
            return stripped
        # Prefix match (first 40 chars): handles trailing args being cut off
        if len(needle) >= 15 and stripped.startswith(needle[:40]):
            return stripped
    return None


async def _codeintel_resolve(
    symbol_name: str,
    relative_path: str,
    project_id: str,
) -> dict[str, Any] | None:
    """Try to resolve *symbol_name* via CodeIntel.

    Returns dict with ``start_line``, ``end_line``, ``signature``, ``source=codeintel``
    on success.

    Raises ``CodeIntelGroundingFailure`` when grounding is unavailable.
    """
    try:
        from backend.clients.codeintel_query_client import codeintel_query_client  # noqa: PLC0415

        ctx, err = await codeintel_query_client.get_context(
            tenant_id="default",
            project_id=project_id,
            entity_query=symbol_name,
            limit=5,
        )
        if err:
            raise CodeIntelGroundingFailure(f"CodeIntel grounding unavailable error={err}")
        if not ctx:
            raise CodeIntelGroundingFailure("CodeIntel grounding unavailable error=empty_context")

        citations = ctx.get("citations", [])
        if not citations:
            raise CodeIntelGroundingFailure("CodeIntel grounding unavailable error=empty_citations")
        # Filter to citations whose file_path ends with relative_path
        rel = relative_path.lstrip("/")
        matching = [c for c in citations if c.get("file_path") and str(c["file_path"]).endswith(rel)]
        best = matching[0] if matching else (citations[0] if citations else None)
        if not best or best.get("start_line") is None:
            raise CodeIntelGroundingFailure("CodeIntel grounding unavailable error=missing_start_line")

        return {
            "start_line": int(best["start_line"]),
            "end_line": int(best.get("end_line") or best["start_line"]),
            "signature": "",  # CodeIntel doesn't return the signature line directly
            "source": "codeintel",
        }
    except CodeIntelGroundingFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CodeIntelGroundingFailure(f"CodeIntel grounding unavailable error={exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_plan_symbols(
    *,
    steps: list[dict[str, Any]],
    workspace_root: str,
    project_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Pre-execution symbol resolution pass.

    Iterates over *steps* and:

    * For symbol-editing steps: resolves ``symbol_name`` to ``_resolved_line_start``
      (and ``_resolved_anchor_quote`` when available) via CodeIntel or AST.
    * For confirm_anchor steps: corrects ``anchor_quote`` to the exact text from
      the file to prevent whitespace-induced ANCHOR_MISMATCH failures.

    Returns ``(resolved_steps, resolver_log, error_code_or_None)``.

    ``resolver_log`` is a list of per-step diagnostics suitable for appending
    to the run diagnostics under key ``"resolver"``.

    ``error_code_or_None`` is non-``None`` only when a symbol is *required* but
    cannot be found at all (returns ``SYMBOL_NOT_FOUND ...``).
    """
    ws = Path(workspace_root)
    resolved: list[dict[str, Any]] = [dict(s) for s in steps]
    log: list[dict[str, Any]] = []

    for i, step in enumerate(resolved):
        tool = step.get("tool", "")
        args: dict[str, Any] = dict(step.get("args") or {})
        step["args"] = args

        # ── Symbol-editing step resolution ────────────────────────────────
        if tool in SYMBOL_EDIT_TOOLS:
            symbol_name = str(args.get("symbol_name") or args.get("symbol") or "").strip()
            relative_path = str(args.get("relative_path") or "").strip()

            if symbol_name and relative_path:
                file_path = ws / relative_path
                # Validators require fully-qualified names (e.g. "module.method").
                # For AST/CodeIntel lookup use just the rightmost simple name.
                simple_name = symbol_name.split(".")[-1] if "." in symbol_name else symbol_name

                # 1. Try CodeIntel
                resolved_info = await _codeintel_resolve(
                    symbol_name=simple_name,
                    relative_path=relative_path,
                    project_id=project_id,
                )

                entry: dict[str, Any] = {
                    "step": "pre_resolve",
                    "step_index": i,
                    "tool": tool,
                    "symbol": symbol_name,
                    "simple_name": simple_name,
                    "file": relative_path,
                }

                if resolved_info is not None:
                    args["_resolved_line_start"] = resolved_info["start_line"]
                    if resolved_info.get("signature"):
                        args["_resolved_anchor_quote"] = resolved_info["signature"]
                    entry["resolved_line"] = resolved_info["start_line"]
                    entry["source"] = resolved_info["source"]
                    entry["status"] = "resolved"
                else:
                    entry["status"] = "not_found"
                    log.append(entry)
                    return (
                        resolved,
                        log,
                        f"SYMBOL_NOT_FOUND symbol={symbol_name} file={relative_path}",
                    )

                log.append(entry)

        # ── Confirm-anchor anchor_quote correction ─────────────────────────
        elif tool == "confirm_anchor":
            anchor_quote = str(args.get("anchor_quote") or "").strip()
            relative_path = str(args.get("relative_path") or "").strip()

            if anchor_quote and relative_path:
                file_path = ws / relative_path
                corrected = _ast_corrected_anchor(anchor_quote, file_path)
                entry = {
                    "step": "pre_resolve",
                    "step_index": i,
                    "tool": "confirm_anchor",
                    "anchor_quote": anchor_quote,
                    "file": relative_path,
                }
                if corrected and corrected != anchor_quote:
                    args["anchor_quote"] = corrected
                    entry["corrected_anchor_quote"] = corrected
                    entry["status"] = "corrected"
                else:
                    entry["status"] = "unchanged" if corrected else "file_not_found"
                log.append(entry)

    return resolved, log, None


__all__ = ["resolve_plan_symbols", "SYMBOL_EDIT_TOOLS", "CodeIntelGroundingFailure"]
