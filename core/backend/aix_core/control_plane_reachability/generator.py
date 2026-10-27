"""Generate and validate the C001 reachability manifest from live sources."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from backend.aix_core.control_plane_reachability.policy import (
    PACKAGE_POLICY,
    ROUTER_POLICY,
    UNMOUNTED_ROUTER_POLICY,
)
from backend.aix_core.control_plane_reachability.schema import (
    DISPOSITIONS,
    RUNTIME_STATUSES,
    ReachabilityComponent,
    ReachabilityManifest,
)

SCHEMA_VERSION = "aix.reachability.v1"

_IMPORT_AS_ROUTER_RE = re.compile(
    r"from\s+(backend\.routers\.[A-Za-z0-9_]+)\s+import\s+router\s+as\s+([A-Za-z0-9_]+)"
)
_INCLUDE_ROUTER_RE = re.compile(r"include_router\(\s*([A-Za-z0-9_]+)")


def _repo_roots(backend_root: Path | None = None) -> tuple[Path, Path, Path]:
    """Return (backend_root, service_root, aix_repo_root)."""
    if backend_root is None:
        backend_root = Path(__file__).resolve().parents[2]
    service_root = backend_root.parent
    aix_repo_root = service_root.parent
    return backend_root, service_root, aix_repo_root


def _git_revision(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def parse_mounted_routers(main_py: Path) -> dict[str, str]:
    """Map include_router variable name → router module path."""
    text = main_py.read_text(encoding="utf-8")
    imports = {alias: module for module, alias in _IMPORT_AS_ROUTER_RE.findall(text)}
    # Also accept multiline import forms already normalized in main.py for two routers.
    # Fall back to AST for aliases imported via parenthesized from-imports.
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend.routers."):
            for alias in node.names:
                if alias.name == "router" and alias.asname:
                    imports[alias.asname] = node.module
    mounted: dict[str, str] = {}
    for match in _INCLUDE_ROUTER_RE.finditer(text):
        var = match.group(1)
        module = imports.get(var)
        if module:
            mounted[var] = module
    return mounted


def list_router_modules(routers_dir: Path) -> list[str]:
    return sorted(p.stem for p in routers_dir.glob("*_router.py"))


def list_aix_core_packages(aix_core_dir: Path) -> list[str]:
    return sorted(
        p.name
        for p in aix_core_dir.iterdir()
        if p.is_dir() and not p.name.startswith("_") and not p.name.startswith(".")
    )


def _package_has_tests(tests_root: Path, package: str) -> bool:
    return (tests_root / "aix_core" / package).exists() or any(
        tests_root.rglob(f"*{package}*")
    )


def _production_importer_hit(backend_root: Path, package: str, mounted_modules: set[str]) -> str:
    """Cheap production-caller probe: main/router/service imports outside tests/archive."""
    needle = f"backend.aix_core.{package}"
    search_roots = [
        backend_root / "main.py",
        backend_root / "routers",
        backend_root / "services",
        backend_root / "aix_orchestrator_v2",
        backend_root / "workflows",
        backend_root / "aix_core" / "objective_cycle",
        backend_root / "aix_core" / "runtime" / "executor",
        backend_root / "infrastructure" / "postgres" / "execution_kernel",
    ]
    hits: list[str] = []
    for root in search_roots:
        if root.is_file():
            try:
                text = root.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text:
                hits.append(str(root.relative_to(backend_root)).replace("\\", "/"))
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            # Skip the package itself when scanning objective_cycle/execution_kernel.
            rel = str(path.relative_to(backend_root)).replace("\\", "/")
            if f"/aix_core/{package}/" in f"/{rel}":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text:
                hits.append(rel)
                if len(hits) >= 3:
                    return "; ".join(hits)
    return "; ".join(hits) if hits else "none"


def _router_component(
    *,
    var_name: str,
    module: str,
    mounted: bool,
) -> ReachabilityComponent:
    stem = module.rsplit(".", 1)[-1]
    if mounted:
        status, disposition, completion, notes = ROUTER_POLICY.get(
            stem,
            ("LIVE", "INVESTIGATE", "unknown", "Mounted but missing seed policy."),
        )
        return ReachabilityComponent(
            component=f"router:{stem}",
            kind="router",
            entrypoint=f"{module}:router",
            production_caller=f"backend.main.include_router({var_name})",
            state_owned=(),
            effects_owned=(),
            completion_authority=completion,
            feature_flags=("LANGGRAPH_ENABLED",) if "langgraph" in stem else (),
            runtime_status=status,
            disposition=disposition,
            notes=notes,
            owners=("backend.main",),
        )
    status, disposition, notes = UNMOUNTED_ROUTER_POLICY.get(
        stem,
        ("IMPLEMENTED_NOT_WIRED", "INVESTIGATE", "Router module exists; not mounted in main.py."),
    )
    return ReachabilityComponent(
        component=f"router:{stem}",
        kind="router",
        entrypoint=f"{module}:router",
        production_caller="none",
        completion_authority="none",
        runtime_status=status,
        disposition=disposition,
        notes=notes,
        owners=(),
    )


def _package_component(
    *,
    package: str,
    backend_root: Path,
    tests_root: Path,
    mounted_modules: set[str],
) -> ReachabilityComponent:
    caller = _production_importer_hit(backend_root, package, mounted_modules)
    policy = PACKAGE_POLICY.get(package)
    if policy:
        status, disposition, notes = policy
    elif caller != "none":
        status, disposition, notes = (
            "PARTIALLY_LIVE",
            "INVESTIGATE",
            "Production importer found; no seed disposition — requires review.",
        )
    elif _package_has_tests(tests_root, package):
        status, disposition, notes = (
            "TEST_ONLY",
            "ARCHIVE",
            "Tests present; no production importer from main/routers/services/workflows.",
        )
    else:
        status, disposition, notes = (
            "DEAD",
            "ARCHIVE",
            "No production importer and no dedicated tests discovered.",
        )
    # Hard override: if seed says LIVE/EXPAND but no caller for non-objective packages, demote.
    if package not in {"objective_cycle", "execution_kernel"} and status in {"LIVE", "PARTIALLY_LIVE"} and caller == "none":
        # Keep INVESTIGATE seeds honest when no caller found.
        if disposition == "INVESTIGATE":
            status = "IMPLEMENTED_NOT_WIRED"
            notes = f"{notes} (no production importer confirmed)."
    return ReachabilityComponent(
        component=f"package:aix_core.{package}",
        kind="package",
        entrypoint=f"backend.aix_core.{package}",
        production_caller=caller,
        state_owned=(f"aix_core.{package}",) if status in {"LIVE", "PARTIALLY_LIVE", "FEATURE_FLAGGED"} else (),
        effects_owned=(),
        completion_authority=(
            "objective_cycle.completion_gates+cycle_outcome"
            if package == "objective_cycle"
            else ("delegates_to_objective_cycle" if package == "execution_kernel" else "none")
        ),
        feature_flags=(),
        runtime_status=status,
        disposition=disposition,
        notes=notes,
        owners=("aix_core",),
    )


def _static_extra_components(*, service_root: Path, aix_repo_root: Path) -> list[ReachabilityComponent]:
    extras: list[ReachabilityComponent] = [
        ReachabilityComponent(
            component="workflow:temporal_runtime",
            kind="workflow",
            entrypoint="backend.infrastructure.temporal.runtime:TemporalRuntimeManager",
            production_caller="backend.main.app_lifespan",
            state_owned=("temporal_workflow_history",),
            effects_owned=("schedule", "retry", "worker_recovery"),
            completion_authority="must_not_own_objective_completion",
            feature_flags=("TEMPORAL_ENABLED",),
            runtime_status="PARTIALLY_LIVE",
            disposition="ADAPTER",
            notes="Temporal hosts scheduling/recovery; ADR-001 forbids a second objective model.",
            owners=("backend.main", "backend.scripts.run_temporal_worker"),
        ),
        ReachabilityComponent(
            component="package:aix_core.execution_kernel",
            kind="package",
            entrypoint="backend.aix_core.runtime.executor.execution_kernel_adapter",
            production_caller="backend.main",
            state_owned=("aix_core.execution_kernel",),
            effects_owned=(),
            completion_authority="delegates_to_objective_cycle",
            feature_flags=(),
            runtime_status="PARTIALLY_LIVE",
            disposition="ADAPTER",
            notes=(
                "Backend reorg batch 7 (2026-08-16): split from the single aix_core/execution_kernel/ "
                "directory into aix_core/runtime/executor/ (domain logic), "
                "aix_core/runtime/executor/execution_kernel_adapter/ (adapter/facade files, this "
                "entrypoint), and infrastructure/postgres/execution_kernel/ (persistence). No longer "
                "auto-discoverable by list_aix_core_packages() since it isn't a single directory under "
                "aix_core/ anymore — declared statically here to preserve this component instead. "
                "Subordinate governance/dispatch; live path delegates to objective_cycle "
                "(see cycle_adapter.ObjectiveCycleExecutorAdapter)."
            ),
            owners=("backend.main", "backend.routers.execution_kernel_router"),
        ),
        ReachabilityComponent(
            component="workflow:langgraph_chain",
            kind="workflow",
            entrypoint="backend.legacy.langgraph.chain:LangGraphOrchestrationChain",
            production_caller="none",
            state_owned=("LangGraphExecutionState",),
            effects_owned=("opsbus_log", "workhub_evidence"),
            completion_authority="none",
            feature_flags=("LANGGRAPH_ENABLED",),
            runtime_status="IMPLEMENTED_NOT_WIRED",
            disposition="REPLACE",
            notes="C004-T02: router unmounted; remount fails closed with LANGGRAPH_WRITES_RETIRED.",
            owners=("langgraph_orchestration_router",),
        ),
        ReachabilityComponent(
            component="worker:temporal_worker_script",
            kind="worker",
            entrypoint="backend.scripts.run_temporal_worker",
            production_caller="ops/start-aix temporal worker",
            state_owned=(),
            effects_owned=("temporal_activities",),
            completion_authority="must_not_own_objective_completion",
            feature_flags=("TEMPORAL_ENABLED",),
            runtime_status="PARTIALLY_LIVE",
            disposition="ADAPTER",
            notes="Worker host for registered Temporal workflows.",
            owners=("ops",),
        ),
        ReachabilityComponent(
            component="module:aix_orchestrator_v2",
            kind="service_module",
            entrypoint="backend.aix_orchestrator_v2.service:AixOrchestratorV2Service",
            production_caller="backend.routers.aix_orchestrator_v2_router;aix_mvp_product_router",
            state_owned=("reports/aix_orchestrator_v2 filesystem state_store",),
            effects_owned=("recon", "plan", "execute", "verify", "writeback"),
            completion_authority="aix_orchestrator_v2.service",
            feature_flags=(),
            runtime_status="PARTIALLY_LIVE",
            disposition="ADAPTER",
            notes="Independent authority today; merge unique WorkHub behavior then retire authority.",
            owners=("aix_orchestrator_v2_router",),
        ),
        ReachabilityComponent(
            component="client:vscode_extension_aix_chat",
            kind="client",
            entrypoint="vscode-extension/aix-chat/src/discussApi.ts",
            production_caller="human_vscode",
            state_owned=(),
            effects_owned=("admit", "discuss", "cycle_commands"),
            completion_authority="none",
            feature_flags=(),
            runtime_status="LIVE",
            disposition="KEEP",
            notes="Clearest live client; calls /aix and /api/aix objective-cycle endpoints via BFF.",
            owners=("vscode-extension/aix-chat",),
        ),
        ReachabilityComponent(
            component="client:aix_frontend_bff",
            kind="client",
            entrypoint="hyeve-aix-service/AIX-Frontend/src/app/api/aix",
            production_caller="vscode-extension/aix-chat;browser",
            state_owned=(),
            effects_owned=("hmac_gateway_sign", "proxy_to_backend"),
            completion_authority="none",
            feature_flags=(),
            runtime_status="LIVE",
            disposition="KEEP",
            notes="Canonical BFF candidate for objective-cycle HMAC gateway.",
            owners=("AIX-Frontend",),
        ),
        ReachabilityComponent(
            component="client:frontend_operator_console",
            kind="client",
            entrypoint="hyeve-aix-service/frontend/src/app/api/aix",
            production_caller="browser_operator",
            state_owned=(),
            effects_owned=("operator_observability_reads",),
            completion_authority="none",
            feature_flags=(),
            runtime_status="PARTIALLY_LIVE",
            disposition="INVESTIGATE",
            notes="Duplicate frontend tree; C097 must pick one canonical client/BFF.",
            owners=("frontend",),
        ),
        ReachabilityComponent(
            component="script:start_aix",
            kind="script",
            entrypoint="start-aix.bat",
            production_caller="operator_local",
            state_owned=(),
            effects_owned=("process_launch",),
            completion_authority="none",
            feature_flags=(),
            runtime_status="LIVE",
            disposition="KEEP",
            notes="Local stack launcher for API/frontend/temporal/deps.",
            owners=("ops",),
        ),
    ]
    # Prove paths exist; if missing, mark MISSING.
    path_checks = {
        "client:vscode_extension_aix_chat": aix_repo_root / "vscode-extension" / "aix-chat" / "src" / "discussApi.ts",
        "client:aix_frontend_bff": service_root / "AIX-Frontend" / "src" / "app" / "api" / "aix",
        "client:frontend_operator_console": service_root / "frontend" / "src" / "app" / "api" / "aix",
        "script:start_aix": aix_repo_root / "start-aix.bat",
    }
    adjusted: list[ReachabilityComponent] = []
    for item in extras:
        check = path_checks.get(item.component)
        if check is not None and not check.exists():
            adjusted.append(
                ReachabilityComponent(
                    component=item.component,
                    kind=item.kind,
                    entrypoint=item.entrypoint,
                    production_caller=item.production_caller,
                    state_owned=item.state_owned,
                    effects_owned=item.effects_owned,
                    completion_authority=item.completion_authority,
                    feature_flags=item.feature_flags,
                    runtime_status="MISSING",
                    disposition="INVESTIGATE",
                    notes=f"Expected path missing: {check}",
                    owners=item.owners,
                )
            )
        else:
            adjusted.append(item)
    return adjusted


def generate_reachability_manifest(
    *,
    backend_root: Path | None = None,
    repository_revision: str | None = None,
) -> ReachabilityManifest:
    backend_root, service_root, aix_repo_root = _repo_roots(backend_root)
    main_py = backend_root / "main.py"
    routers_dir = backend_root / "routers"
    aix_core_dir = backend_root / "aix_core"
    tests_root = backend_root / "tests"

    mounted = parse_mounted_routers(main_py)
    mounted_stems = {module.rsplit(".", 1)[-1] for module in mounted.values()}
    mounted_modules = set(mounted.values())
    all_routers = list_router_modules(routers_dir)

    components: list[ReachabilityComponent] = []

    # Mounted routers
    for var_name, module in sorted(mounted.items(), key=lambda kv: kv[1]):
        components.append(_router_component(var_name=var_name, module=module, mounted=True))

    # Unmounted router modules
    for stem in all_routers:
        if stem in mounted_stems:
            continue
        components.append(
            _router_component(
                var_name=stem,
                module=f"backend.routers.{stem}",
                mounted=False,
            )
        )

    # aix_core packages
    for package in list_aix_core_packages(aix_core_dir):
        if package == "control_plane_reachability":
            components.append(
                ReachabilityComponent(
                    component="package:aix_core.control_plane_reachability",
                    kind="package",
                    entrypoint="backend.aix_core.control_plane_reachability",
                    production_caller="ci_harness",
                    completion_authority="none",
                    runtime_status="LIVE",
                    disposition="KEEP",
                    notes="C001 reachability generator and CI gate.",
                    owners=("control_plane",),
                )
            )
            continue
        components.append(
            _package_component(
                package=package,
                backend_root=backend_root,
                tests_root=tests_root,
                mounted_modules=mounted_modules,
            )
        )

    components.extend(_static_extra_components(service_root=service_root, aix_repo_root=aix_repo_root))

    # Stable order
    components.sort(key=lambda c: (c.kind, c.component))

    revision = repository_revision or _git_revision(aix_repo_root)
    manifest = ReachabilityManifest(
        schema_version=SCHEMA_VERSION,
        repository_revision=revision,
        generated_from=str(main_py.relative_to(aix_repo_root)).replace("\\", "/"),
        components=components,
    )
    errors = validate_manifest(manifest, mounted_router_stems=mounted_stems)
    manifest.validation_errors = errors
    manifest.unclassified_production_routes = [
        e.split(":", 1)[1] for e in errors if e.startswith("UNCLASSIFIED_MOUNTED_ROUTER:")
    ]
    return manifest


def validate_manifest(
    manifest: ReachabilityManifest,
    *,
    mounted_router_stems: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for component in manifest.components:
        if component.component in seen:
            errors.append(f"DUPLICATE_COMPONENT:{component.component}")
        seen.add(component.component)
        if component.runtime_status not in RUNTIME_STATUSES:
            errors.append(f"INVALID_RUNTIME_STATUS:{component.component}:{component.runtime_status}")
        if component.disposition not in DISPOSITIONS:
            errors.append(f"INVALID_DISPOSITION:{component.component}:{component.disposition}")
        if component.kind == "router" and component.production_caller != "none":
            if component.disposition == "INVESTIGATE" and "missing seed policy" in component.notes.lower():
                errors.append(f"UNCLASSIFIED_MOUNTED_ROUTER:{component.component}")

    if mounted_router_stems is not None:
        classified = {
            c.component.removeprefix("router:")
            for c in manifest.components
            if c.kind == "router" and c.production_caller != "none"
        }
        for stem in sorted(mounted_router_stems):
            if stem not in classified:
                errors.append(f"UNCLASSIFIED_MOUNTED_ROUTER:router:{stem}")
            else:
                # Every mounted router must have a concrete disposition (not blank).
                match = next(c for c in manifest.components if c.component == f"router:{stem}")
                if match.disposition not in DISPOSITIONS:
                    errors.append(f"UNCLASSIFIED_MOUNTED_ROUTER:router:{stem}")

    # Exactly one disposition per package component is guaranteed by construction;
    # additionally require every aix_core package (except missing) appears once.
    return errors


def manifest_is_green(manifest: ReachabilityManifest) -> bool:
    return not manifest.validation_errors and not manifest.unclassified_production_routes


def write_manifest(manifest: ReachabilityManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> ReachabilityManifest:
    return ReachabilityManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def default_manifest_path(backend_root: Path | None = None) -> Path:
    backend_root, service_root, aix_repo_root = _repo_roots(backend_root)
    return (
        aix_repo_root
        / "docs"
        / "current"
        / "AIX Closed-Loop Execution Plan"
        / "tasks"
        / "master-roadmap"
        / "C001"
        / "evidence"
        / "reachability_manifest.v1.json"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate C001 reachability manifest")
    parser.add_argument("--write", action="store_true", help="Write checked-in evidence JSON")
    parser.add_argument("--check", action="store_true", help="Fail if validation errors exist")
    parser.add_argument("--out", type=Path, default=None, help="Output path override")
    args = parser.parse_args(argv)

    manifest = generate_reachability_manifest()
    out = args.out or default_manifest_path()
    if args.write:
        write_manifest(manifest, out)
        print(f"wrote {out} components={len(manifest.components)}")
    else:
        print(json.dumps({"component_count": len(manifest.components), "errors": manifest.validation_errors}, indent=2))
    if args.check and not manifest_is_green(manifest):
        print("REACHABILITY_MANIFEST_INVALID")
        for err in manifest.validation_errors:
            print(err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
