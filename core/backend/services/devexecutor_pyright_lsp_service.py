from __future__ import annotations

import ast
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PYRIGHT_LSP_COMMAND = "pyright-langserver --stdio"
_LSP_REQUEST_TIMEOUT_SECONDS = 10.0
_LSP_DIAGNOSTIC_TIMEOUT_SECONDS = 5.0
_LSP_MAX_DOCUMENT_SYMBOL_TIMEOUT_SECONDS = 60.0
_MAX_CACHED_DAEMONS = 8


def _pyright_lsp_enabled() -> bool:
    raw = str(os.environ.get("AIX_ENABLE_PYRIGHT_LSP", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True, slots=True)
class PyrightLspSymbolBounds:
    symbol_name: str
    qualified_name: str
    symbol_type: str
    start_line: int
    def_line: int
    def_col: int
    end_line: int
    decorator_lines: tuple[int, ...] = ()
    symbol_source: str = ""
    semantic_valid: bool = False
    diagnostics: tuple[str, ...] = ()
    resolution_source: str = "pyright_lsp"


@dataclass(frozen=True, slots=True)
class PyrightLspValidationResult:
    available: bool
    passed: bool
    output: str
    diagnostics: tuple[str, ...]


class PyrightLspDaemon:
    def __init__(self, *, workspace_root: str | Path, command: list[str] | None = None) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._command = command or _pyright_lsp_command()
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._request_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._diagnostics: dict[str, dict[str, Any]] = {}
        self._document_versions: dict[str, int] = {}
        self._diagnostic_events: dict[str, threading.Event] = {}
        self._startup_error: str | None = None
        self._last_used_monotonic = time.monotonic()

    @property
    def available(self) -> bool:
        self.ensure_started()
        return self._proc is not None and self._proc.poll() is None

    @property
    def last_used_monotonic(self) -> float:
        return self._last_used_monotonic

    def ensure_started(self) -> None:
        with self._lock:
            self._last_used_monotonic = time.monotonic()
            if self._proc is not None and self._proc.poll() is None:
                return
            if not _pyright_lsp_enabled():
                self._startup_error = "pyright_lsp_disabled"
                self._proc = None
                return
            self._startup_error = None
            try:
                self._proc = subprocess.Popen(
                    self._command,
                    cwd=str(self._workspace_root),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                self._startup_error = str(exc)
                self._proc = None
                logger.warning("Pyright LSP unavailable for %s: %s", self._workspace_root, exc)
                return
            self._reader_thread = threading.Thread(target=self._reader_loop, name="pyright-lsp-reader", daemon=True)
            self._reader_thread.start()
            self._request(
                "initialize", {"processId": None, "rootUri": self._workspace_root.as_uri(), "capabilities": {}}
            )
            self._notify("initialized", {})

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._pending.clear()
            self._diagnostics.clear()
            self._document_versions.clear()
            self._diagnostic_events.clear()
            if proc is None:
                return
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr is not None:
                    proc.stderr.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def resolve_symbol(
        self,
        *,
        file_path: str | Path,
        symbol_name: str,
        symbol_type: str = "function",
    ) -> PyrightLspSymbolBounds | None:
        path = Path(file_path).resolve()
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            return None
        diagnostics = self._open_or_update_document(path=path, text=source)
        symbols = self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": path.as_uri()}},
            timeout_seconds=_document_symbol_request_timeout_seconds(source),
        )
        symbol = _select_document_symbol(
            symbol_items=symbols if isinstance(symbols, list) else [],
            symbol_name=symbol_name,
            symbol_type=symbol_type,
            source=source,
            path=path,
            diagnostics=diagnostics,
        )
        self._notify("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
        return symbol

    def validate_document_change(
        self,
        *,
        file_path: str | Path,
        original_text: str,
        updated_text: str,
    ) -> PyrightLspValidationResult:
        self.ensure_started()
        if self._proc is None:
            output = self._startup_error or "pyright_lsp_unavailable"
            return PyrightLspValidationResult(available=False, passed=False, output=output, diagnostics=())
        path = Path(file_path).resolve()
        self._open_or_update_document(path=path, text=original_text)
        diagnostics = self._open_or_update_document(path=path, text=updated_text)
        self._notify("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
        messages = tuple(_diagnostic_message(item) for item in diagnostics if _diagnostic_severity(item) <= 1)
        syntax_message = _python_syntax_error_message(updated_text)
        if syntax_message:
            messages = (*messages, syntax_message)
        output = "\n".join(messages)
        return PyrightLspValidationResult(
            available=True,
            passed=not messages,
            output=output,
            diagnostics=messages,
        )

    def validate_document_batch(
        self,
        *,
        updates: list[tuple[str | Path, str, str]],
    ) -> dict[str, PyrightLspValidationResult]:
        self.ensure_started()
        if self._proc is None:
            output = self._startup_error or "pyright_lsp_unavailable"
            return {
                str(Path(file_path).resolve()): PyrightLspValidationResult(
                    available=False,
                    passed=False,
                    output=output,
                    diagnostics=(),
                )
                for file_path, _original_text, _updated_text in updates
            }

        resolved_updates = [
            (Path(file_path).resolve(), original_text, updated_text)
            for file_path, original_text, updated_text in updates
        ]
        for path, original_text, _updated_text in resolved_updates:
            self._open_or_update_document(path=path, text=original_text)

        diagnostics_by_path: dict[str, list[dict[str, Any]]] = {}
        for path, _original_text, updated_text in resolved_updates:
            diagnostics_by_path[str(path)] = self._open_or_update_document(path=path, text=updated_text)

        results: dict[str, PyrightLspValidationResult] = {}
        try:
            for path, _original_text, updated_text in resolved_updates:
                diagnostics = diagnostics_by_path.get(str(path), [])
                messages = tuple(_diagnostic_message(item) for item in diagnostics if _diagnostic_severity(item) <= 1)
                syntax_message = _python_syntax_error_message(updated_text)
                if syntax_message:
                    messages = (*messages, syntax_message)
                output = "\n".join(messages)
                results[str(path)] = PyrightLspValidationResult(
                    available=True,
                    passed=not messages,
                    output=output,
                    diagnostics=messages,
                )
        finally:
            for path, _original_text, _updated_text in resolved_updates:
                self._notify("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
        return results

    def _open_or_update_document(self, *, path: Path, text: str) -> list[dict[str, Any]]:
        self.ensure_started()
        if self._proc is None:
            return []
        uri = path.as_uri()
        event = self._diagnostic_events.setdefault(uri, threading.Event())
        event.clear()
        self._diagnostics.pop(uri, None)
        current_version = self._document_versions.get(uri, 0)
        if current_version <= 0:
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "python",
                        "version": 1,
                        "text": text,
                    }
                },
            )
            self._document_versions[uri] = 1
        else:
            next_version = current_version + 1
            self._notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": next_version},
                    "contentChanges": [{"text": text}],
                },
            )
            self._document_versions[uri] = next_version
        self._notify("textDocument/didSave", {"textDocument": {"uri": uri}})
        event.wait(timeout=_LSP_DIAGNOSTIC_TIMEOUT_SECONDS)
        params = self._diagnostics.get(uri) or {}
        diagnostics = params.get("diagnostics")
        return [item for item in diagnostics if isinstance(item, dict)] if isinstance(diagnostics, list) else []

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while proc.poll() is None:
                message = _read_lsp_message(proc.stdout)
                if message is None:
                    return
                if "id" in message and ("result" in message or "error" in message):
                    response_id = int(message["id"])
                    pending = self._pending.get(response_id)
                    if pending is not None:
                        pending.put(message)
                    continue
                if message.get("method") == "textDocument/publishDiagnostics":
                    raw_params = message.get("params")
                    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
                    uri = str(params.get("uri") or "")
                    if uri:
                        self._diagnostics[uri] = params
                        event = self._diagnostic_events.setdefault(uri, threading.Event())
                        event.set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pyright LSP reader loop stopped for %s: %s", self._workspace_root, exc)

    def _request(self, method: str, params: dict[str, Any], *, timeout_seconds: float | None = None) -> Any:
        self.ensure_started()
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(self._startup_error or "pyright_lsp_unavailable")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
            _write_lsp_message(
                self._proc.stdin,
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            )
        try:
            response = response_queue.get(timeout=timeout_seconds or _LSP_REQUEST_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise RuntimeError(f"pyright_lsp_timeout method={method}") from exc
        finally:
            self._pending.pop(request_id, None)
        if isinstance(response.get("error"), dict):
            message = str(response["error"].get("message") or "pyright_lsp_error")
            raise RuntimeError(message)
        return response.get("result")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self.ensure_started()
        if self._proc is None or self._proc.stdin is None:
            return
        with self._lock:
            _write_lsp_message(self._proc.stdin, {"jsonrpc": "2.0", "method": method, "params": params})


_DAEMONS: dict[str, PyrightLspDaemon] = {}
_DAEMONS_LOCK = threading.Lock()


def get_pyright_lsp_daemon(*, workspace_root: str | Path) -> PyrightLspDaemon:
    normalized = str(Path(workspace_root).resolve())
    with _DAEMONS_LOCK:
        stale_keys = [
            key for key, candidate in _DAEMONS.items() if candidate._proc is None or candidate._proc.poll() is not None
        ]
        for key in stale_keys:
            daemon = _DAEMONS.pop(key, None)
            if daemon is not None:
                daemon.close()
        daemon = _DAEMONS.get(normalized)
        if daemon is None:
            if len(_DAEMONS) >= _MAX_CACHED_DAEMONS:
                oldest_key = min(_DAEMONS, key=lambda item: _DAEMONS[item].last_used_monotonic)
                oldest = _DAEMONS.pop(oldest_key)
                oldest.close()
            daemon = PyrightLspDaemon(workspace_root=normalized)
            _DAEMONS[normalized] = daemon
        return daemon


def shutdown_all_pyright_lsp_daemons() -> None:
    with _DAEMONS_LOCK:
        daemons = list(_DAEMONS.values())
        _DAEMONS.clear()
    for daemon in daemons:
        daemon.close()


def resolve_python_symbol_bounds_via_lsp(
    *,
    file_path: str | Path,
    symbol_name: str,
    symbol_type: str = "function",
) -> PyrightLspSymbolBounds | None:
    path = Path(file_path).resolve()
    daemon = get_pyright_lsp_daemon(workspace_root=_infer_workspace_root(path))
    if not daemon.available:
        return None
    try:
        return daemon.resolve_symbol(file_path=path, symbol_name=symbol_name, symbol_type=symbol_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pyright LSP symbol resolution failed for %s:%s: %s", path, symbol_name, exc)
        return None


def validate_python_change_via_lsp(
    *,
    workspace_root: str | Path,
    file_path: str | Path,
    original_text: str,
    updated_text: str,
) -> PyrightLspValidationResult:
    daemon = get_pyright_lsp_daemon(workspace_root=workspace_root)
    try:
        return daemon.validate_document_change(
            file_path=file_path,
            original_text=original_text,
            updated_text=updated_text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pyright LSP pre-apply validation failed for %s: %s", file_path, exc)
        return PyrightLspValidationResult(available=False, passed=False, output=str(exc), diagnostics=())


def validate_python_changes_via_lsp(
    *,
    workspace_root: str | Path,
    updates: list[tuple[str | Path, str, str]],
) -> dict[str, PyrightLspValidationResult]:
    daemon = get_pyright_lsp_daemon(workspace_root=workspace_root)
    try:
        return daemon.validate_document_batch(updates=updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pyright LSP batch pre-apply validation failed for %s: %s", workspace_root, exc)
        return {
            str(Path(file_path).resolve()): PyrightLspValidationResult(
                available=False,
                passed=False,
                output=str(exc),
                diagnostics=(),
            )
            for file_path, _original_text, _updated_text in updates
        }


def resolve_pyright_cli_command() -> list[str]:
    raw = str(os.environ.get("PYRIGHT_COMMAND") or "pyright").strip()
    parts = raw.split()
    if not parts:
        return ["pyright"]
    command_name = parts[0]
    if os.name == "nt" and not Path(command_name).suffix:
        for candidate in (
            command_name,
            f"{command_name}.cmd",
            f"{command_name}.exe",
            f"{command_name}.bat",
        ):
            resolved = shutil.which(candidate)
            if resolved:
                parts[0] = resolved
                break
    return parts


def pyright_cli_available() -> bool:
    command = resolve_pyright_cli_command()
    if not command:
        return False
    executable = command[0]
    return Path(executable).exists() or shutil.which(executable) is not None


def _infer_workspace_root(path: Path) -> Path:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return current


def _pyright_lsp_command() -> list[str]:
    raw = str(os.environ.get("PYRIGHT_LANGSERVER_COMMAND") or _DEFAULT_PYRIGHT_LSP_COMMAND).strip()
    parts = raw.split()
    if not parts:
        return []
    command_name = parts[0]
    if os.name == "nt" and not Path(command_name).suffix:
        for candidate in (
            command_name,
            f"{command_name}.cmd",
            f"{command_name}.exe",
            f"{command_name}.bat",
        ):
            resolved = shutil.which(candidate)
            if resolved:
                parts[0] = resolved
                break
    return parts


def _document_symbol_request_timeout_seconds(source: str) -> float:
    line_count = source.count("\n") + 1 if source else 1
    byte_count = len(source.encode("utf-8")) if source else 0
    size_budget = max(line_count / 1000.0, byte_count / 100000.0)
    if size_budget <= 1.0:
        return _LSP_REQUEST_TIMEOUT_SECONDS
    return min(_LSP_MAX_DOCUMENT_SYMBOL_TIMEOUT_SECONDS, max(_LSP_REQUEST_TIMEOUT_SECONDS, 20.0 + size_budget))


def _read_lsp_message(stream: Any) -> dict[str, Any] | None:
    content_length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if decoded.lower().startswith("content-length:"):
            content_length = int(decoded.split(":", 1)[1].strip())
    if content_length <= 0:
        return None
    payload = stream.read(content_length)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8", errors="replace"))


def _write_lsp_message(stream: Any, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
    stream.write(header)
    stream.write(encoded)
    stream.flush()


def _select_document_symbol(
    *,
    symbol_items: list[dict[str, Any]],
    symbol_name: str,
    symbol_type: str,
    source: str,
    path: Path,
    diagnostics: list[dict[str, Any]],
) -> PyrightLspSymbolBounds | None:
    requested_symbol = str(symbol_name or "").strip()
    fallback_symbol = _module_qualified_function_fallback(path=path, symbol_name=requested_symbol)
    requested_candidates = [requested_symbol]
    if fallback_symbol:
        requested_candidates.append(fallback_symbol)
    flattened = _flatten_document_symbols(symbol_items)
    matches: list[dict[str, Any]] = []
    for candidate in requested_candidates:
        matches = [
            item for item in flattened if _symbol_matches(item=item, requested=candidate, symbol_type=symbol_type)
        ]
        if matches:
            break
    if len(matches) != 1:
        return None
    selected = matches[0]
    symbol_diagnostics = tuple(
        _diagnostic_message(item)
        for item in diagnostics
        if _diagnostic_overlaps(item=item, start_line=int(selected["start_line"]), end_line=int(selected["end_line"]))
        and _diagnostic_severity(item) <= 1
    )
    start_line = int(selected["start_line"])
    end_line = int(selected["end_line"])
    lines = source.splitlines()
    snippet = "\n".join(lines[start_line - 1 : end_line]).rstrip()
    def_line = int(selected["def_line"])
    def_col = int(selected["def_col"])
    for line_number in range(start_line, end_line + 1):
        candidate = lines[line_number - 1]
        stripped = candidate.lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            def_line = line_number
            def_col = len(candidate) - len(stripped)
            break
    decorator_lines = tuple(
        line_number for line_number in range(start_line, def_line) if lines[line_number - 1].lstrip().startswith("@")
    )
    return PyrightLspSymbolBounds(
        symbol_name=str(selected["symbol_name"]),
        qualified_name=str(selected["qualified_name"]),
        symbol_type=str(selected["symbol_type"]),
        start_line=start_line,
        def_line=def_line,
        def_col=def_col,
        end_line=end_line,
        decorator_lines=decorator_lines,
        symbol_source=snippet,
        semantic_valid=not symbol_diagnostics,
        diagnostics=symbol_diagnostics,
    )


def _flatten_document_symbols(symbol_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def _walk(item: dict[str, Any], parent_qualified: str = "") -> None:
        name = str(item.get("name") or "").strip()
        if not name:
            return
        qualified_name = f"{parent_qualified}.{name}" if parent_qualified else name
        raw_location = item.get("location")
        location: dict[str, Any] = raw_location if isinstance(raw_location, dict) else {}
        raw_location_range = location.get("range")
        location_range: dict[str, Any] = raw_location_range if isinstance(raw_location_range, dict) else {}
        raw_range_node = item.get("range")
        range_node: dict[str, Any] = raw_range_node if isinstance(raw_range_node, dict) else location_range
        raw_selection = item.get("selectionRange")
        selection: dict[str, Any] = raw_selection if isinstance(raw_selection, dict) else range_node
        raw_start = range_node.get("start")
        start: dict[str, Any] = raw_start if isinstance(raw_start, dict) else {}
        raw_end = range_node.get("end")
        end: dict[str, Any] = raw_end if isinstance(raw_end, dict) else {}
        raw_selection_start = selection.get("start")
        selection_start: dict[str, Any] = raw_selection_start if isinstance(raw_selection_start, dict) else start
        flattened.append(
            {
                "symbol_name": name,
                "qualified_name": qualified_name,
                "symbol_type": _symbol_kind_to_type(int(item.get("kind") or 0)),
                "start_line": int(start.get("line", 0)) + 1,
                "end_line": int(end.get("line", 0)) + 1,
                "def_line": int(selection_start.get("line", start.get("line", 0))) + 1,
                "def_col": int(selection_start.get("character", start.get("character", 0))),
            }
        )
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    _walk(child, qualified_name)

    for item in symbol_items:
        if isinstance(item, dict):
            _walk(item)
    return flattened


def _symbol_matches(*, item: dict[str, Any], requested: str, symbol_type: str) -> bool:
    normalized_requested = str(requested or "").strip()
    if not normalized_requested:
        return False
    item_type = str(item.get("symbol_type") or "")
    normalized_type = str(symbol_type or "function").strip().lower() or "function"
    if normalized_type == "function" and item_type not in {"function", "async_function", "method"}:
        return False
    if normalized_type == "class" and item_type != "class":
        return False
    qualified_name = str(item.get("qualified_name") or "")
    simple_name = str(item.get("symbol_name") or "")
    if qualified_name == normalized_requested or simple_name == normalized_requested:
        return True
    return qualified_name.endswith(f".{normalized_requested}")


def _symbol_kind_to_type(kind: int) -> str:
    if kind == 5:
        return "class"
    if kind == 6:
        return "method"
    if kind == 12:
        return "function"
    return "symbol"


def _diagnostic_severity(item: dict[str, Any]) -> int:
    try:
        return int(item.get("severity", 1))
    except (TypeError, ValueError):
        return 1


def _diagnostic_message(item: dict[str, Any]) -> str:
    message = str(item.get("message") or "").strip()
    if not message:
        return "pyright diagnostic"
    return message


def _diagnostic_overlaps(*, item: dict[str, Any], start_line: int, end_line: int) -> bool:
    raw_range_node = item.get("range")
    range_node: dict[str, Any] = raw_range_node if isinstance(raw_range_node, dict) else {}
    raw_start = range_node.get("start")
    start: dict[str, Any] = raw_start if isinstance(raw_start, dict) else {}
    raw_end = range_node.get("end")
    end: dict[str, Any] = raw_end if isinstance(raw_end, dict) else {}
    diagnostic_start = int(start.get("line", 0)) + 1
    diagnostic_end = int(end.get("line", diagnostic_start - 1)) + 1
    return diagnostic_end >= start_line and diagnostic_start <= end_line


def _module_qualified_function_fallback(*, path: Path, symbol_name: str) -> str:
    normalized = str(symbol_name or "").strip()
    if normalized.count(".") < 1:
        return ""
    parts = [part.strip() for part in normalized.split(".") if part.strip()]
    if len(parts) < 2:
        return ""
    requested_module = ".".join(parts[:-1])
    requested_symbol = parts[-1]
    path_parts = [part.strip() for part in path.with_suffix("").parts if part.strip()]
    normalized_path = ".".join(part.replace("-", "_") for part in path_parts)
    if normalized_path == requested_module or normalized_path.endswith(f".{requested_module}"):
        return requested_symbol
    return ""


def _python_syntax_error_message(source: str) -> str:
    try:
        ast.parse(source)
    except SyntaxError as exc:
        message = str(exc.msg or "invalid syntax").strip() or "invalid syntax"
        line = int(exc.lineno or 0)
        column = int(exc.offset or 0)
        if line > 0:
            return f"Syntax error at line {line}:{column} - {message}"
        return f"Syntax error - {message}"
    return ""


__all__ = [
    "PyrightLspDaemon",
    "PyrightLspSymbolBounds",
    "PyrightLspValidationResult",
    "get_pyright_lsp_daemon",
    "resolve_python_symbol_bounds_via_lsp",
    "shutdown_all_pyright_lsp_daemons",
    "resolve_pyright_cli_command",
    "pyright_cli_available",
    "validate_python_change_via_lsp",
    "validate_python_changes_via_lsp",
]
