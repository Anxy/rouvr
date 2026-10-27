"""AIX-PD-003B — ProductStructureV1 → governed PlanTargets compiler.

Pure control plane: no LLM, no I/O, no ERP/product hard-coding.

Authoritative inputs:
  sealed OutcomeContractV1
  ProductStructureV1 (PD-003A)
  Governance create_root / CREATE capability
  Intent text (product root + supporting filenames only)

Pages come from structure views (e.g. view ref \"dashboard\" → dashboard.html).
Supporting stack files come from Intent names or facet-justified generic defaults
under the Intent-derived product root — never from a domain page catalog.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.outcome_obligations import sanitize_obligation_token
from backend.aix_core.runtime.objective.product_class import infer_greenfield_product_slug
from backend.aix_core.runtime.objective.product_structure import (
    ProductStructureV1,
    compile_product_structure,
)
from backend.aix_core.runtime.objective.provenance.contracts import (
    CreateCapability,
    PlanTarget,
)
from backend.aix_core.runtime.objective.provenance.validators import (
    create_root_contains,
    normalize_rel_path,
)

PLAN_COMPILER_VERSION = "v1"

TargetRole = Literal[
    "DIRECTORY",
    "UI_PAGE",
    "UI_ASSET",
    "API_BACKEND",
    "DB_SCHEMA",
    "SUPPORTING",
]

_SLUG_RE = re.compile(r"(?i)\bproposed[/\\]([\w.-]+)\b")
_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])("
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\."
    r"(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css|txt)"
    r"|[A-Za-z0-9_.-]+\."
    r"(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css|txt)"
    r")"
)
_UI_ASSET_NAMES = frozenset({"styles.css", "app.js", "main.js", "style.css"})
_SUPPORT_NAMES = frozenset({"readme.md", "config.php", "config.py", "config.json", ".env.example"})


@dataclass(frozen=True)
class TargetTrace:
    path: str
    role: TargetRole
    structure_ref: str
    contract_facet: str


@dataclass(frozen=True)
class ProductStructurePlanResult:
    ok: bool
    code: str
    detail: str
    targets: tuple[PlanTarget, ...] = ()
    step_scopes: tuple[tuple[str, ...], ...] = ()
    traces: tuple[TargetTrace, ...] = ()
    product_root: str | None = None
    depends_on: tuple[tuple[int, ...], ...] = ()  # per-step dependency indexes


def _norm_dir(path: str) -> str:
    text = (normalize_rel_path(path) or path).replace("\\", "/").strip()
    if text and not text.endswith("/"):
        text = text + "/"
    return text


def _norm_file(path: str) -> str:
    return (normalize_rel_path(path) or path).replace("\\", "/").strip().rstrip("/")


def _target_id(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:20]
    return f"pt_{digest}"


def _filename(path: str) -> str:
    return _norm_file(path).rsplit("/", 1)[-1]


def resolve_product_root(
    *,
    intent_text: str,
    create_roots: tuple[str, ...],
) -> tuple[str | None, str | None, str]:
    """Return (product_root_dir, error_code, detail)."""
    roots = create_roots or ("proposed/",)
    text = str(intent_text or "")
    candidates: list[str] = []

    slug_m = _SLUG_RE.search(text)
    if slug_m:
        candidates.append(_norm_dir(f"proposed/{slug_m.group(1)}"))

    named: list[str] = []
    for match in _PATH_RE.finditer(text):
        raw = match.group(1).replace("\\", "/")
        if "/" not in raw and slug_m:
            raw = f"proposed/{slug_m.group(1)}/{raw}"
        norm = _norm_file(raw)
        if any(create_root_contains(target=norm, root=root) for root in roots):
            named.append(norm)

    parents = sorted(
        {
            _norm_dir(p.rsplit("/", 1)[0] + "/")
            for p in named
            if "/" in p
        }
    )
    for parent in parents:
        if parent not in candidates:
            candidates.append(parent)

    # Keep only under create_root
    candidates = [
        c
        for c in candidates
        if any(create_root_contains(target=c.rstrip("/"), root=root) or create_root_contains(target=c, root=root) for root in roots)
        or any(_norm_dir(root) == c or c.startswith(_norm_dir(root)) for root in roots)
    ]
    # Deduplicate preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)

    if not uniq:
        slug = infer_greenfield_product_slug(objective=text)
        if slug:
            inferred = _norm_dir(f"proposed/{slug}")
            if any(
                create_root_contains(target=inferred.rstrip("/"), root=root)
                or _norm_dir(root) == inferred
                or inferred.startswith(_norm_dir(root))
                for root in roots
            ):
                uniq = [inferred]
        if not uniq:
            return None, "PRODUCT_ROOT_MISSING", "no product root under create_root in Intent"
    # Prefer deepest non-create_root-only directory (e.g. proposed/erp-system/ over proposed/)
    non_root = [c for c in uniq if c.rstrip("/") not in {r.rstrip("/") for r in roots}]
    pool = non_root or uniq
    if len(pool) > 1:
        # Ambiguous if two disjoint product dirs
        pool_sorted = sorted(pool, key=lambda p: (-p.count("/"), p))
        deepest = pool_sorted[0]
        others = [p for p in pool_sorted[1:] if not (deepest.startswith(p) or p.startswith(deepest))]
        if others:
            return None, "PRODUCT_ROOT_AMBIGUOUS", f"multiple product roots: {pool_sorted}"
        return deepest, None, "ok"
    return pool[0], None, "ok"


def _intent_named_files(*, intent_text: str, product_root: str, create_roots: tuple[str, ...]) -> tuple[str, ...]:
    roots = create_roots or ("proposed/",)
    text = str(intent_text or "")
    slug = product_root.rstrip("/")
    found: list[str] = []
    seen: set[str] = set()
    for match in _PATH_RE.finditer(text):
        raw = match.group(1).replace("\\", "/")
        if "/" not in raw:
            raw = f"{slug}/{raw}"
        norm = _norm_file(raw)
        if not any(create_root_contains(target=norm, root=root) for root in roots):
            continue
        if not (norm == slug or norm.startswith(slug + "/")):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        found.append(norm)
    return tuple(sorted(found))


def _stack_ext(intent_text: str, *, kind: Literal["api", "config"]) -> str | None:
    text = intent_text.casefold()
    if kind == "api":
        if ".php" in text or re.search(r"\bphp\b", text):
            return "php"
        if (
            "fastapi" in text
            or "flask" in text
            or ".py" in text
            or re.search(r"\bpython\b", text)
        ):
            return "py"
        if ".ts" in text or "typescript" in text:
            return "ts"
        if ".js" in text or re.search(r"\bjavascript\b", text) or re.search(r"\bnode\b", text):
            return "js"
        return None
    if ".php" in text or re.search(r"\bphp\b", text):
        return "php"
    if ".py" in text or re.search(r"\bpython\b", text):
        return "py"
    if ".json" in text:
        return "json"
    return None


def _detect_cycle(depends_on: list[list[int]]) -> bool:
    n = len(depends_on)
    visiting = [False] * n
    visited = [False] * n

    def dfs(i: int) -> bool:
        if visiting[i]:
            return True
        if visited[i]:
            return False
        visiting[i] = True
        for j in depends_on[i]:
            if 0 <= j < n and dfs(j):
                return True
        visiting[i] = False
        visited[i] = True
        return False

    return any(dfs(i) for i in range(n))


def compile_product_structure_plan(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1 | None = None,
    intent_text: str,
    create_roots: tuple[str, ...],
    create_capability: tuple[CreateCapability, ...] | tuple[str, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
    outcome_claim_ids: tuple[str, ...],
    allowed_create_root: str | None = None,
) -> ProductStructurePlanResult:
    """Compile governed CREATE PlanTargets from ProductStructure + governance + Intent root."""
    if not outcome_claim_ids:
        return ProductStructurePlanResult(
            ok=False,
            code="OUTCOME_CLAIM_REQUIRED",
            detail="≥1 ACTIVE OUTCOME claim id required for PlanTarget provenance",
        )
    caps = tuple(create_capability or ())
    if "CREATE_FILE" not in caps:
        return ProductStructurePlanResult(
            ok=False,
            code="GOVERNANCE_CREATE_REQUIRED",
            detail="CREATE_FILE capability required",
        )

    if structure is None:
        ps = compile_product_structure(contract)
        if not ps.ok or ps.structure is None:
            return ProductStructurePlanResult(
                ok=False,
                code=ps.code if not ps.ok else "PRODUCT_STRUCTURE_REQUIRED",
                detail=ps.detail,
            )
        structure = ps.structure

    if structure.contract_hash != contract.contract_hash:
        return ProductStructurePlanResult(
            ok=False,
            code="STRUCTURE_CONTRACT_MISMATCH",
            detail="ProductStructureV1.contract_hash must match sealed contract",
        )

    roots = tuple(create_roots or ())
    if not roots:
        return ProductStructurePlanResult(
            ok=False,
            code="GOVERNANCE_CREATE_REQUIRED",
            detail="create_root required",
        )

    product_root, root_err, root_detail = resolve_product_root(
        intent_text=intent_text, create_roots=roots
    )
    if root_err or not product_root:
        return ProductStructurePlanResult(
            ok=False,
            code=root_err or "PRODUCT_ROOT_MISSING",
            detail=root_detail,
        )

    create_root = allowed_create_root or roots[0]
    create_root = create_root if create_root.endswith("/") else create_root + "/"
    # Normalize create_root to the governance root that contains product_root
    matched_root = None
    for root in roots:
        r = root if root.endswith("/") else root + "/"
        if create_root_contains(target=product_root.rstrip("/"), root=root) or product_root.startswith(
            _norm_dir(root)
        ):
            matched_root = r
            break
    if matched_root is None:
        return ProductStructurePlanResult(
            ok=False,
            code="SCOPE_WIDENING",
            detail=f"product_root {product_root!r} outside create_root {roots!r}",
        )
    create_root = matched_root

    facets = contract.facets
    intent_files = _intent_named_files(
        intent_text=intent_text, product_root=product_root.rstrip("/"), create_roots=roots
    )

    # path -> (role, structure_ref, facet)
    planned: dict[str, tuple[TargetRole, str, str]] = {}

    def _add(path: str, role: TargetRole, structure_ref: str, facet: str) -> str | None:
        norm = _norm_file(path) if role != "DIRECTORY" else _norm_dir(path).rstrip("/") + "/"
        if role == "DIRECTORY":
            norm = _norm_dir(path)
        else:
            norm = _norm_file(path)
        if not create_root_contains(target=norm.rstrip("/"), root=create_root) and not (
            role == "DIRECTORY" and create_root_contains(target=norm.rstrip("/"), root=create_root)
        ):
            # directories: path ends with /
            check = norm.rstrip("/")
            if not create_root_contains(target=check, root=create_root):
                return "SCOPE_WIDENING"
        if norm in planned and planned[norm][0] != role:
            return "DUPLICATE_TARGET"
        if norm in planned:
            return None
        planned[norm] = (role, structure_ref, facet)
        return None

    err = _add(product_root, "DIRECTORY", "product_root", "GOVERNANCE")
    if err:
        return ProductStructurePlanResult(ok=False, code=err, detail=f"product_root {product_root}")

    # UI pages from structure views (authoritative page set — not ERP catalog)
    if facets.UI.requirement == "MANDATORY" or structure.views:
        for view in structure.views:
            token = sanitize_obligation_token(view.ref) or "view"
            page = f"{product_root.rstrip('/')}/{token}.html"
            err = _add(page, "UI_PAGE", view.view_id, "UI")
            if err:
                return ProductStructurePlanResult(
                    ok=False, code=err, detail=f"view page {page}"
                )

    # Intent-named files under product_root — classify by extension / name
    view_basenames = {
        f"{sanitize_obligation_token(v.ref)}.html" for v in structure.views
    }
    for path in intent_files:
        base = _filename(path).casefold()
        ext = base.rsplit(".", 1)[-1] if "." in base else ""
        if base.endswith("/") or path.endswith("/"):
            continue
        if base in view_basenames or any(
            path.endswith(f"/{sanitize_obligation_token(v.ref)}.html") for v in structure.views
        ):
            # Already emitted as UI_PAGE (or same path)
            if _norm_file(path) not in planned:
                # Intent named a page that matches a view with different sanitization — skip orphan
                pass
            continue
        if ext == "html":
            # Shell/entry html (e.g. index.html) — UI facet supporting page
            if facets.UI.requirement == "ABSENT":
                return ProductStructurePlanResult(
                    ok=False,
                    code="TARGET_NOT_TRACEABLE",
                    detail=f"html {path} without UI facet",
                )
            err = _add(path, "UI_ASSET", "intent_ui_shell", "UI")
        elif base in _UI_ASSET_NAMES or ext in {"css", "js"}:
            if facets.UI.requirement == "ABSENT":
                return ProductStructurePlanResult(
                    ok=False,
                    code="TARGET_NOT_TRACEABLE",
                    detail=f"ui asset {path} without UI facet",
                )
            err = _add(path, "UI_ASSET", "intent_ui_asset", "UI")
        elif ext in {"php", "py", "ts", "js"} and (
            base.startswith("api") or "api" in base or base in {"app.php", "server.py", "index.php"}
        ):
            if facets.API.requirement == "ABSENT":
                return ProductStructurePlanResult(
                    ok=False,
                    code="TARGET_NOT_TRACEABLE",
                    detail=f"api {path} without API facet",
                )
            err = _add(path, "API_BACKEND", "intent_api", "API")
        elif ext == "sql" or base.startswith("database") or base.startswith("schema"):
            if facets.DB.requirement == "ABSENT":
                return ProductStructurePlanResult(
                    ok=False,
                    code="TARGET_NOT_TRACEABLE",
                    detail=f"db {path} without DB facet",
                )
            err = _add(path, "DB_SCHEMA", "intent_db", "DB")
        elif base in _SUPPORT_NAMES or ext in {"md", "json", "yml", "yaml", "txt"} or base.startswith("config"):
            err = _add(path, "SUPPORTING", "intent_support", "GOVERNANCE")
        else:
            return ProductStructurePlanResult(
                ok=False,
                code="UNSUPPORTED_TARGET_TYPE",
                detail=f"unsupported Intent file {path}",
            )
        if err:
            return ProductStructurePlanResult(ok=False, code=err, detail=path)

    # Facet-required defaults when Intent omitted stack files (still not domain pages)
    if facets.API.requirement == "MANDATORY":
        has_api = any(role == "API_BACKEND" for role, _, _ in planned.values())
        if not has_api:
            ext = _stack_ext(intent_text, kind="api")
            if not ext:
                return ProductStructurePlanResult(
                    ok=False,
                    code="AMBIGUOUS_TARGET",
                    detail="API facet mandatory but no Intent API file / stack extension",
                )
            err = _add(
                f"{product_root.rstrip('/')}/api.{ext}",
                "API_BACKEND",
                "api_default",
                "API",
            )
            if err:
                return ProductStructurePlanResult(ok=False, code=err, detail="api default")

    if facets.DB.requirement == "MANDATORY":
        has_db = any(role == "DB_SCHEMA" for role, _, _ in planned.values())
        if not has_db:
            if not re.search(
                r"(?i)\b(sql|sqlite|mysql|postgres|database|schema)\b", intent_text
            ):
                return ProductStructurePlanResult(
                    ok=False,
                    code="AMBIGUOUS_TARGET",
                    detail="DB facet mandatory but no Intent schema file / sql hint",
                )
            err = _add(
                f"{product_root.rstrip('/')}/database.sql",
                "DB_SCHEMA",
                "db_default",
                "DB",
            )
            if err:
                return ProductStructurePlanResult(ok=False, code=err, detail="db default")

    if facets.UI.requirement == "MANDATORY":
        has_page = any(role == "UI_PAGE" for role, _, _ in planned.values())
        if not has_page and not structure.views:
            return ProductStructurePlanResult(
                ok=False,
                code="PRODUCT_PLAN_EMPTY",
                detail="UI mandatory but no views to materialize as pages",
            )
        has_index = any(_filename(p) == "index.html" for p in planned)
        if not has_index:
            err = _add(
                f"{product_root.rstrip('/')}/index.html",
                "UI_ASSET",
                "ui_shell_default",
                "UI",
            )
            if err:
                return ProductStructurePlanResult(ok=False, code=err, detail="index default")
        has_css = any(_filename(p) in {"styles.css", "style.css"} for p in planned)
        if not has_css and re.search(r"(?i)\bcss\b", intent_text):
            err = _add(
                f"{product_root.rstrip('/')}/styles.css",
                "UI_ASSET",
                "ui_css_default",
                "UI",
            )
            if err:
                return ProductStructurePlanResult(ok=False, code=err, detail="css default")
        has_js = any(_filename(p).endswith(".js") for p in planned)
        if not has_js and re.search(r"(?i)\bjavascript\b|\.js\b", intent_text):
            err = _add(
                f"{product_root.rstrip('/')}/app.js",
                "UI_ASSET",
                "ui_js_default",
                "UI",
            )
            if err:
                return ProductStructurePlanResult(ok=False, code=err, detail="js default")

    if re.search(r"(?i)\breadme\b", intent_text):
        err = _add(
            f"{product_root.rstrip('/')}/README.md",
            "SUPPORTING",
            "readme_default",
            "GOVERNANCE",
        )
        if err:
            return ProductStructurePlanResult(ok=False, code=err, detail="readme default")
    if re.search(r"(?i)\b(pytest|api tests|automated tests)\b", intent_text):
        err = _add(
            f"{product_root.rstrip('/')}/tests/test_api.py",
            "SUPPORTING",
            "tests_default",
            "GOVERNANCE",
        )
        if err:
            return ProductStructurePlanResult(ok=False, code=err, detail="tests default")

    root_prefix = product_root.rstrip("/") + "/"
    for path, (role, _, _) in list(planned.items()):
        if role == "DIRECTORY":
            continue
        parts = path.replace("\\", "/").split("/")
        for i in range(len(parts) - 1, 1, -1):
            parent = "/".join(parts[:i]) + "/"
            if parent == product_root or not parent.startswith(root_prefix):
                break
            err = _add(parent, "DIRECTORY", "parent_dir", "GOVERNANCE")
            if err:
                return ProductStructurePlanResult(ok=False, code=err, detail=parent)

    # Orphan check: every UI_PAGE must map to a structure view
    view_ids = {v.view_id for v in structure.views}
    for path, (role, href, _facet) in planned.items():
        if role == "UI_PAGE" and href not in view_ids:
            return ProductStructurePlanResult(
                ok=False,
                code="ORPHAN_TARGET",
                detail=f"UI page {path} not tied to ProductStructure view",
            )

    if len(planned) <= 1:  # only directory
        return ProductStructurePlanResult(
            ok=False,
            code="PRODUCT_PLAN_EMPTY",
            detail="no file PlanTargets derived from structure/contract",
            product_root=product_root,
        )

    # Build ordered groups for step_scopes + depends_on
    groups: list[tuple[str, list[str]]] = [
        ("DIRECTORY", []),
        ("DB_SCHEMA", []),
        ("API_BACKEND", []),
        ("UI_ASSET", []),
        ("UI_PAGE", []),
        ("SUPPORTING", []),
    ]
    role_to_group = {name: idx for idx, (name, _) in enumerate(groups)}
    for path in sorted(planned.keys()):
        role, _, _ = planned[path]
        groups[role_to_group[role]][1].append(path)

    step_scopes_list: list[tuple[str, ...]] = []
    step_roles: list[str] = []
    for role_name, paths in groups:
        if paths:
            step_scopes_list.append(tuple(paths))
            step_roles.append(role_name)

    depends: list[list[int]] = [[] for _ in step_scopes_list]
    role_index = {r: i for i, r in enumerate(step_roles)}
    # Ordering: DIRECTORY ← all; DB ← API; API ← UI_*; UI_ASSET ← UI_PAGE; SUPPORTING after pages
    for i, role_name in enumerate(step_roles):
        if role_name != "DIRECTORY" and "DIRECTORY" in role_index:
            depends[i].append(role_index["DIRECTORY"])
        if role_name == "API_BACKEND" and "DB_SCHEMA" in role_index:
            depends[i].append(role_index["DB_SCHEMA"])
        if role_name in {"UI_ASSET", "UI_PAGE"} and "API_BACKEND" in role_index:
            depends[i].append(role_index["API_BACKEND"])
        if role_name == "UI_PAGE" and "UI_ASSET" in role_index:
            depends[i].append(role_index["UI_ASSET"])
        if role_name == "SUPPORTING":
            for pred in ("UI_PAGE", "UI_ASSET", "API_BACKEND", "DB_SCHEMA"):
                if pred in role_index:
                    depends[i].append(role_index[pred])
                    break

    # Dedup depends
    depends = [sorted(set(d)) for d in depends]
    if _detect_cycle(depends):
        return ProductStructurePlanResult(
            ok=False,
            code="CYCLIC_DEPENDENCY",
            detail="step dependency cycle detected",
            product_root=product_root,
        )

    can_dir = "CREATE_DIRECTORY" in caps
    targets: list[PlanTarget] = []
    traces: list[TargetTrace] = []
    for path in sorted(planned.keys()):
        role, structure_ref, facet = planned[path]
        if role == "DIRECTORY":
            if not can_dir:
                return ProductStructurePlanResult(
                    ok=False,
                    code="GOVERNANCE_CREATE_REQUIRED",
                    detail="CREATE_DIRECTORY required for product root",
                )
            capability: CreateCapability = "CREATE_DIRECTORY"
            effect = "LITERAL_REPLACE"
            rationale = (
                f"PD-003B CREATE directory for product root; "
                f"trace={structure_ref}; facet={facet}"
            )
        else:
            capability = "CREATE_FILE"
            effect = "LITERAL_REPLACE"
            rationale = (
                f"PD-003B CREATE {role} from ProductStructure/OutcomeContract; "
                f"trace={structure_ref}; facet={facet}"
            )
        targets.append(
            PlanTarget(
                id=_target_id(path),
                kind="CREATE",
                path=path,
                effect=effect,  # type: ignore[arg-type]
                rationale=rationale,
                create_capability=capability,
                create_root=create_root.rstrip("/") + "/" if not create_root.endswith("/") else create_root,
                outcome_claim_ids=outcome_claim_ids,
            )
        )
        traces.append(
            TargetTrace(path=path, role=role, structure_ref=structure_ref, contract_facet=facet)
        )

    return ProductStructurePlanResult(
        ok=True,
        code="PRODUCT_STRUCTURE_PLAN_COMPILED",
        detail=(
            f"compiled {len(targets)} PlanTargets under {product_root} "
            f"({len(step_scopes_list)} ordered steps)"
        ),
        targets=tuple(targets),
        step_scopes=tuple(step_scopes_list),
        traces=tuple(traces),
        product_root=product_root,
        depends_on=tuple(tuple(d) for d in depends),
    )


__all__ = [
    "PLAN_COMPILER_VERSION",
    "TargetRole",
    "TargetTrace",
    "ProductStructurePlanResult",
    "resolve_product_root",
    "compile_product_structure_plan",
]
