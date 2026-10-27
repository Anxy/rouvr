"""AIX Closed-Loop Execution -- Sprint 003 (`AIX-CLOSED-LOOP-003`), Packet CL3-P1.

Completion (a diff exists, is substantive by line count, and mechanical checks pass) is
not the same thing as objective coverage (the diff actually did what the objective named).
The incident this sprint fixes (`cyc_f45ad72bf0c6`, see `.validation/closed-loop-003-
baseline.md`) is a +48/-9 import-banner restyle that passed every existing gate while
doing none of the objective's three named asks (extract repeated DB/query boilerplate,
remove unused use-imports, group with section comments).

This module is deliberately generic: every signal is derived from **objective text**
(never a filename or a hardcoded controller name) and checked against the unified diff
(+ an optional pre-image snippet, for "already satisfied before this diff ran"). It adds
one mechanical gate on top of `refactor_change_is_substantive`'s line-count floor --
neither replaces the other (`completion_gates.py`'s `Recommended implementation rules`:
"coverage is an additional mechanical gate before 'PASS means the asked work happened'").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Objective-text triggers per signal (case-insensitive substring match). Adding a
# trigger here only ever widens which objectives are *checked* for that signal --
# it never changes what counts as satisfying evidence in the diff/pre-image.
_SIGNAL_TRIGGERS: dict[str, tuple[str, ...]] = {
    "extract_helpers": ("boilerplate", "helper method", "helper methods", "repeated"),
    "extract_to_new_types": (
        "service class",
        "service classes",
        "layered architecture",
        "thin orchestrator",
        "domain-focused",
        "domain focused",
        "into those services",
        "into a dedicated",
        "into dedicated",
        "separated responsibilities",
        "extract at least",
        "god class",
        "god-class",
    ),
    "remove_unused_imports": (
        "unused use-import", "unused use-imports", "unused import", "unused imports",
    ),
    "section_comments": ("section comment", "section comments", "grouped as"),
    # Operator/UI product asks must mutate a real view surface, not only typings
    # (live FE#1: CycleSummary types in a client lib + git diff --check = false PASS).
    "ui_surface_change": (
        "summary strip",
        "cycle summary",
        "live execution",
        "operator pages",
        "operator ui",
        "objectives list",
        "include archived",
        "include-archived",
        "archived objective",
        "show phase",
        "display a",
        "ui or api tests",
        "fixture-mode warning",
        "fixture mode warning",
        "waiting for human approval",
        "awaiting human approval",
        "actively running",
        "approval versus",
        "approval vs",
        "approval-waiting",
        "phase/status",
        "phase clarity",
    ),
    "playwright_cert_coverage": (
        "playwright",
        "product-certification",
        "product certification",
        "browser certification",
    ),
    "focused_tests": (
        "focused tests",
        "add focused tests",
        "add tests proving",
        "focused tests proving",
    ),
}


def objective_requires_focused_tests(objective: str) -> bool:
    """True when the objective explicitly asks for new focused tests, not just to run tests."""
    text = str(objective or "").casefold()
    return any(tok in text for tok in _SIGNAL_TRIGGERS["focused_tests"])

# `regroup_methods` overlaps `section_comments` in most real objective phrasing ("group
# ... with section comments") and verifying actual method *reordering* is explicitly out
# of scope for this packet (CL2-P2 note: "Method reordering is out of scope unless a
# later packet proves it with AST-equivalent safety") -- named here for the vocabulary
# record (see baseline doc) but deliberately not independently gated below.
REGROUP_METHODS_TRIGGERS: tuple[str, ...] = ("regroup", "restructure")

_IMPORT_LINE_RE = re.compile(
    r"^\s*(use\s+[\w\\]+(\s+as\s+\w+)?\s*;|import\s+[\w.*{}\s,]+|from\s+[\w.]+\s+import\s+)",
    re.IGNORECASE,
)
_FUNC_DEF_RE = re.compile(r"\b(?:function|def)\s+(\w+)\s*\(")
_SECTION_BANNER_RE = re.compile(r"^\s*(//|#|/\*|\*)\s*[-=]{3,}")


@dataclass(frozen=True)
class ObjectiveCoverageResult:
    satisfied: bool
    triggered_signals: tuple[str, ...]
    missing_signals: tuple[str, ...]
    detail: str


def _diff_lines(diff_text: str, prefix: str) -> list[str]:
    header = "---" if prefix == "-" else "+++"
    out: list[str] = []
    for line in str(diff_text or "").splitlines():
        if not line.startswith(prefix) or line.startswith(header) or line.startswith("@@"):
            continue
        out.append(line[1:])
    return out


def _objective_signals(objective: str) -> frozenset[str]:
    lowered = str(objective or "").casefold()
    triggered = {
        name for name, triggers in _SIGNAL_TRIGGERS.items() if any(t in lowered for t in triggers)
    }
    # Bare "extract" (without architectural language) still means in-file helpers.
    if "extract" in lowered and "extract_to_new_types" not in triggered:
        triggered.add("extract_helpers")
    if "extract_to_new_types" in triggered:
        triggered.discard("extract_helpers")
    # Operator UI "extract shared fetch/error/empty" is a TS component factoring
    # ask, not the PHP repeated-query helper signal (live FE#7).
    if "extract_helpers" in triggered and any(
        tok in lowered
        for tok in (
            "operator",
            "panels",
            "empty-state",
            "empty state",
            "reusable helpers or components",
            "fetch, error",
            "loading, error",
        )
    ):
        triggered.discard("extract_helpers")
    # Playwright cert asks are satisfied by spec/config edits, not UI surfaces.
    if "playwright_cert_coverage" in triggered:
        triggered.discard("ui_surface_change")
    return frozenset(triggered)


def _unused_imports_removed(diff_text: str) -> bool:
    """A generic (PHP `use`, Python `import`/`from ... import`, JS/TS `import`) removed
    import line counts as evidence the signal was acted on. Whether the import was
    *actually* unused is not re-derived here -- that judgment already happened wherever
    the removal was produced (a deterministic fallback pass, or an LLM patch); this gate
    only asks whether removal evidence exists in the diff at all."""
    return any(_IMPORT_LINE_RE.match(line) for line in _diff_lines(diff_text, "-"))


def _extract_helper_added_and_reused(diff_text: str) -> bool:
    """A genuine extraction introduces a new function/method definition AND calls it
    from at least 2 distinct sites in the added lines -- a single-call-site wrapper
    (like the incident's own `centralDatabaseName()`, called exactly once) is not an
    extraction of *repeated* boilerplate, it is a rename with extra steps."""
    added = _diff_lines(diff_text, "+")
    added_text = "\n".join(added)
    defined_names = {m.group(1) for m in _FUNC_DEF_RE.finditer(added_text)}
    for name in defined_names:
        # Count call-like references to this name elsewhere in the added lines,
        # excluding the definition line itself.
        call_re = re.compile(r"\b" + re.escape(name) + r"\s*\(")
        call_sites = sum(
            1 for line in added
            if call_re.search(line) and not re.search(r"\b(?:function|def)\s+" + re.escape(name) + r"\s*\(", line)
        )
        if call_sites >= 2:
            return True
    return False


def _extract_to_new_types_done(diff_text: str) -> bool:
    """Architectural extract requires multiple new type files, not in-file helpers."""
    lines = str(diff_text or "").splitlines()
    new_file_headers: list[str] = []
    for index, line in enumerate(lines):
        if not line.startswith("+++ b/"):
            continue
        path = line[6:].strip()
        prev = lines[index - 1] if index > 0 else ""
        if prev.startswith("--- /dev/null"):
            new_file_headers.append(path)
            continue
        if prev.startswith("--- a/"):
            window: list[str] = []
            for nxt in lines[index + 1 :]:
                if nxt.startswith("--- ") and window:
                    break
                if nxt.startswith("+++ ") and window:
                    break
                window.append(nxt)
            added = [w for w in window if w.startswith("+") and not w.startswith("+++")]
            removed = [w for w in window if w.startswith("-") and not w.startswith("---")]
            if added and not removed and any(re.search(r"\bclass\s+\w+", a[1:]) for a in added):
                new_file_headers.append(path)
    class_service_adds = sum(
        1
        for line in lines
        if line.startswith("+")
        and not line.startswith("+++")
        and re.search(r"\bclass\s+\w*Service\b", line[1:])
    )
    delegates = sum(
        1
        for line in lines
        if line.startswith("+")
        and not line.startswith("+++")
        and re.search(r"new\s+\w+Service\s*\(", line[1:])
    )
    return len(set(new_file_headers)) >= 3 and class_service_adds >= 3 and delegates >= 1


def _section_banner_present(text: str) -> bool:
    return any(_SECTION_BANNER_RE.match(line) for line in str(text or "").splitlines())


def _section_comments_addressed(diff_text: str, pre_image_snippet: str) -> bool:
    if _section_banner_present(pre_image_snippet):
        return True  # already satisfied before this diff -- not this diff's job
    return any(_SECTION_BANNER_RE.match(line) for line in _diff_lines(diff_text, "+"))


def _ui_surface_changed(diff_text: str) -> bool:
    """True when the diff touches a view/route surface (tsx/jsx/vue/html page/component)."""
    surface_ext = (".tsx", ".jsx", ".vue", ".html", ".css", ".scss")
    surface_dirs = ("/components/", "/app/", "/pages/", "/views/", "/ui/")
    for line in str(diff_text or "").splitlines():
        if not (line.startswith("+++ b/") or line.startswith("--- a/")):
            continue
        path = line[6:].strip().replace("\\", "/").casefold()
        if path.endswith(surface_ext):
            return True
        if any(part in path for part in surface_dirs) and path.endswith((".ts", ".js", ".tsx", ".jsx")):
            # Prefer component/page trees over pure lib typings.
            if "/lib/" in path or path.endswith("client.ts") or path.endswith("-client.ts"):
                continue
            return True
    # Also accept added JSX/TSX markup lines even if path headers are odd.
    for line in _diff_lines(diff_text, "+"):
        if re.search(r"<\w+[\s/>]", line) and ("className=" in line or "data-" in line or "</" in line):
            return True
    return False


def _playwright_cert_changed(diff_text: str) -> bool:
    for line in str(diff_text or "").splitlines():
        if not (line.startswith("+++ b/") or line.startswith("--- a/")):
            continue
        path = line[6:].strip().replace("\\", "/").casefold()
        if "playwright" in path and path.endswith((".ts", ".js", ".mjs")):
            return True
        if path.endswith(("playwright.config.ts", "playwright.config.js")):
            return True
    return False


def _playwright_cert_present_in_files(files: dict[str, str] | None) -> bool:
    if not files:
        return False
    for path, content in files.items():
        norm = str(path).replace("\\", "/").casefold()
        body = str(content or "")
        if "playwright" not in norm and not norm.endswith("playwright.config.ts"):
            continue
        if "objectives" in body.casefold() and (
            "phase" in body.casefold() or "status" in body.casefold()
        ):
            return True
    return False


def _focused_tests_added(diff_text: str, _pre_image: str = "") -> bool:
    """A named tests ask is satisfied only when a real test file appears in the diff."""
    for line in str(diff_text or "").splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            norm = path.replace("\\", "/").casefold()
            name = norm.rsplit("/", 1)[-1]
            if name.startswith("test_") or name.endswith((".spec.ts", ".test.ts", ".test.js")):
                return True
            if "/tests/" in norm and name.endswith((".py", ".ts", ".js", ".tsx")):
                return True
    return False


_SIGNAL_CHECKS = {
    "remove_unused_imports": lambda diff_text, pre_image: _unused_imports_removed(diff_text),
    "extract_helpers": lambda diff_text, pre_image: _extract_helper_added_and_reused(diff_text),
    "extract_to_new_types": lambda diff_text, pre_image: _extract_to_new_types_done(diff_text),
    "section_comments": lambda diff_text, pre_image: _section_comments_addressed(diff_text, pre_image),
    "ui_surface_change": lambda diff_text, pre_image: _ui_surface_changed(diff_text),
    "playwright_cert_coverage": lambda diff_text, pre_image: _playwright_cert_changed(diff_text),
    "focused_tests": lambda diff_text, pre_image: _focused_tests_added(diff_text, pre_image),
}


def _ui_surface_markers_for_objective(objective: str) -> tuple[str, ...]:
    """Concrete UI presence markers for product asks that often land on untracked trees."""
    text = str(objective or "").casefold()
    markers: list[str] = []
    if any(
        tok in text
        for tok in (
            "include archived",
            "include-archived",
            "archived objective",
            "objectives list",
        )
    ):
        markers.extend(
            (
                'data-testid="include-archived-objectives"',
                "includeArchived",
            )
        )
    if any(tok in text for tok in ("cycle summary", "summary strip")):
        markers.extend(
            (
                'data-testid="cycle-summary-strip"',
                "CycleSummaryStrip",
            )
        )
    if any(tok in text for tok in ("fixture-mode warning", "fixture mode warning", "fixture warning")):
        markers.extend(("FixtureModeWarning", "fixture-mode-warning"))
    if any(
        tok in text
        for tok in (
            "waiting for human approval",
            "awaiting human approval",
            "actively running",
            "approval versus",
            "approval vs",
            "approval-waiting",
            "phase/status",
            "phase clarity",
        )
    ):
        markers.extend(
            (
                'data-cycle-state="approval-waiting"',
                'data-cycle-state="actively-running"',
                "Awaiting human approval",
                "Actively running",
            )
        )
    return tuple(markers)


def _ui_surface_present_in_files(objective: str, files: dict[str, str] | None) -> bool:
    markers = _ui_surface_markers_for_objective(objective)
    if not markers or not files:
        return False
    for path, content in files.items():
        norm = str(path).replace("\\", "/").casefold()
        if not norm.endswith((".tsx", ".jsx", ".vue", ".html")):
            continue
        if "/lib/" in norm:
            continue
        body = str(content or "")
        if any(marker in body for marker in markers):
            return True
    return False


def check_objective_coverage(
    *,
    objective: str,
    diff_text: str,
    task_family: str = "",
    pre_image_snippet: str = "",
    workspace_files: dict[str, str] | None = None,
) -> ObjectiveCoverageResult:
    """Only meaningful for REFACTOR/EDIT-shaped objectives -- an objective that
    triggers no signal is not this gate's concern (`satisfied=True`, N/A, matches
    CL3-P1's own acceptance rule: unrelated bound EDITs must not fail here)."""
    triggered = _objective_signals(objective)
    if not triggered:
        return ObjectiveCoverageResult(
            satisfied=True, triggered_signals=(), missing_signals=(),
            detail="objective names no extract/unused-import/section-comment signal -- N/A",
        )

    missing = tuple(
        sorted(
            name for name in triggered
            if not _SIGNAL_CHECKS[name](diff_text, pre_image_snippet)
        )
    )
    # UI product asks: accept workspace presence when the diff is lib-only or empty
    # after a prior untracked landing (live FE#2).
    if (
        missing == ("ui_surface_change",)
        and _ui_surface_present_in_files(objective, workspace_files)
    ):
        missing = ()
    if (
        "playwright_cert_coverage" in missing
        and _playwright_cert_present_in_files(workspace_files)
    ):
        missing = tuple(name for name in missing if name != "playwright_cert_coverage")
    if missing:
        return ObjectiveCoverageResult(
            satisfied=False,
            triggered_signals=tuple(sorted(triggered)),
            missing_signals=missing,
            detail=f"objective asked for {list(missing)} but the diff does not show it done",
        )
    return ObjectiveCoverageResult(
        satisfied=True, triggered_signals=tuple(sorted(triggered)), missing_signals=(),
        detail="all objective-named signals addressed by this diff or already satisfied",
    )


def objective_requires_ui_surface(objective: str) -> bool:
    return "ui_surface_change" in _objective_signals(objective)


__all__ = [
    "ObjectiveCoverageResult",
    "REGROUP_METHODS_TRIGGERS",
    "check_objective_coverage",
    "objective_requires_ui_surface",
    "_ui_surface_present_in_files",
]
