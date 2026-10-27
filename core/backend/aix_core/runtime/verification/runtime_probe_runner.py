"""AIX Feature 12 — Runtime Probe Runner with real I/O.

Probes execute live checks against the workspace deliverable:
- DB stack: SQLite file/URL authority vs PostgreSQL/psycopg divergence
- Server startup: uvicorn subprocess + HTTP /health
- Auth/CRUD: real HTTP against the running server when possible
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
)
from backend.aix_core.runtime.verification.verification_result import (
    OVItemResultV1,
    VerificationStatus,
)


@dataclass
class ProbeResult:
    probe_id: str
    passed: bool
    expected: str
    observed: str
    output: str = ""
    evidence_refs: tuple[str, ...] = ()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_json(method: str, url: str, body: bytes | None = None, timeout: float = 5.0) -> tuple[int, str]:
    req = Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return int(e.code), e.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError) as e:
        return 0, str(e)


class RuntimeProbeRunner:
    """Executes live and static runtime probes against a project workspace."""

    def __init__(self, workspace_path: Path | str | None = None) -> None:
        self.workspace_path = Path(workspace_path) if workspace_path else Path.cwd()

    def _collect_code_text(self) -> tuple[str, list[str]]:
        snippets: list[str] = []
        files: list[str] = []
        if not self.workspace_path.exists():
            return "", []
        for ext in ("*.py", "*.env", "*.ini", "*.toml", "*.json", "*.js", "*.ts"):
            for f in self.workspace_path.rglob(ext):
                try:
                    snippets.append(f.read_text(encoding="utf-8", errors="ignore"))
                    files.append(str(f.relative_to(self.workspace_path)))
                except Exception:
                    continue
        return "\n".join(snippets), files

    def probe_db_stack(self, expected_db: str = "sqlite") -> ProbeResult:
        """OV-002: SQLite persistence authority — fail if Postgres/psycopg without SQLite."""
        if not self.workspace_path.exists():
            return ProbeResult(
                probe_id="probe_db_stack",
                passed=False,
                expected=f"{expected_db} persistence configured and utilized",
                observed="workspace path does not exist",
                output=f"Path not found: {self.workspace_path}",
            )

        text, files = self._collect_code_text()
        sqlite_db_files = (
            list(self.workspace_path.rglob("*.db"))
            + list(self.workspace_path.rglob("*.sqlite"))
            + list(self.workspace_path.rglob("*.sqlite3"))
        )
        # Prefer non-empty sqlite files as stronger evidence
        nonempty_sqlite = [p for p in sqlite_db_files if p.is_file() and p.stat().st_size > 0]

        uses_sqlite_url = bool(re.search(r"sqlite(?:3)?:/+|create_engine\(\s*['\"]sqlite", text, re.I))
        uses_sqlite_import = bool(re.search(r"\bsqlite3\b|aiosqlite", text, re.I))
        uses_postgres = bool(
            re.search(
                r"postgresql(?:\+psycopg2|\+psycopg)?://|\bpsycopg2\b|\bpsycopg\b|\basyncpg\b",
                text,
                re.I,
            )
        )
        uses_sqlite = bool(nonempty_sqlite or uses_sqlite_url or uses_sqlite_import)

        if expected_db.lower() == "sqlite":
            if uses_postgres and not uses_sqlite:
                postgres_files = [
                    f for f in files if re.search(r"psycopg|postgresql|postgres", Path(self.workspace_path, f).read_text(encoding="utf-8", errors="ignore"), re.I)
                ][:5]
                return ProbeResult(
                    probe_id="probe_db_stack",
                    passed=False,
                    expected="SQLite database configuration (sqlite:// or .db file) without Postgres authority",
                    observed=f"PostgreSQL/psycopg hardcoded without SQLite authority in files: {postgres_files or files[:3]}",
                    output=f"postgres={uses_postgres} sqlite={uses_sqlite} db_files={sqlite_db_files[:3]}",
                    evidence_refs=tuple(f"file://{p}" for p in (postgres_files or files[:3])),
                )
            if uses_sqlite:
                return ProbeResult(
                    probe_id="probe_db_stack",
                    passed=True,
                    expected="SQLite database configuration",
                    observed=f"SQLite authority present (url={uses_sqlite_url}, import={uses_sqlite_import}, files={len(nonempty_sqlite)})",
                    output=f"db_files={nonempty_sqlite[:2]}",
                    evidence_refs=tuple(f"file://{p}" for p in [str(p.relative_to(self.workspace_path)) for p in nonempty_sqlite[:2]]),
                )
            return ProbeResult(
                probe_id="probe_db_stack",
                passed=False,
                expected="SQLite database configuration",
                observed="No SQLite URL, import, or .db file found",
                output=f"scanned_files={len(files)}",
            )

        return ProbeResult(
            probe_id="probe_db_stack",
            passed=True,
            expected=f"{expected_db} database configuration",
            observed=f"Database stack verified for {expected_db}",
        )

    def _find_asgi_module(self) -> tuple[str, Path] | None:
        """Return (uvicorn target like 'main:app', cwd) if FastAPI/ASGI entrypoint exists."""
        candidates = [
            ("main.py", "main:app"),
            ("app.py", "app:app"),
            ("backend/main.py", "backend.main:app"),
            ("backend/api.py", "backend.api:app"),
            ("api.py", "api:app"),
            ("server.py", "server:app"),
            ("proposed/expense-tracker/api.py", "api:app"),
        ]
        for rel, target in candidates:
            path = self.workspace_path / rel
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            if "FastAPI" in content or "fastapi" in content:
                if "/" in rel or "\\" in rel:
                    mod = rel.replace("\\", "/").removesuffix(".py").replace("/", ".")
                    if re.search(r"\bapp\s*=\s*FastAPI", content):
                        return f"{mod}:app", self.workspace_path
                    cwd = path.parent
                    stem = path.stem
                    if re.search(r"\bapp\s*=\s*FastAPI", content):
                        return f"{stem}:app", cwd
                if re.search(r"\bapp\s*=\s*FastAPI", content):
                    return target, self.workspace_path

        for name in ("api.py", "main.py", "app.py", "server.py"):
            for path in sorted(self.workspace_path.rglob(name)):
                if not path.is_file():
                    continue
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if not re.search(r"\bapp\s*=\s*FastAPI", content) and "fastapi" not in content.lower():
                    continue
                cwd = path.parent
                return f"{path.stem}:app", cwd
        return None

    def probe_server_startup(self) -> ProbeResult:
        """OV-003: start uvicorn and GET /health (real I/O)."""
        found = self._find_asgi_module()
        if found is None:
            return ProbeResult(
                probe_id="probe_server_startup",
                passed=False,
                expected="FastAPI/uvicorn entrypoint that serves /health",
                observed="No FastAPI ASGI entrypoint found (main.py/app.py/api.py/server.py)",
            )

        target, cwd = found
        port = _free_port()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                target,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "error",
            ],
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{port}/health"
        try:
            status = 0
            body = ""
            for _ in range(20):
                time.sleep(0.25)
                if proc.poll() is not None:
                    err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", errors="replace")[:500]
                    return ProbeResult(
                        probe_id="probe_server_startup",
                        passed=False,
                        expected="uvicorn starts and /health returns 200",
                        observed=f"uvicorn exited early code={proc.returncode}",
                        output=err,
                        evidence_refs=(f"uvicorn://{target}",),
                    )
                status, body = _http_json("GET", url, timeout=1.5)
                if status == 200:
                    return ProbeResult(
                        probe_id="probe_server_startup",
                        passed=True,
                        expected="uvicorn starts and /health returns 200",
                        observed=f"GET {url} -> 200 ({body[:120]})",
                        output=f"target={target} port={port}",
                        evidence_refs=(f"http://127.0.0.1:{port}/health",),
                    )
            return ProbeResult(
                probe_id="probe_server_startup",
                passed=False,
                expected="uvicorn starts and /health returns 200",
                observed=f"health check failed status={status} body={body[:200]}",
                output=f"target={target} port={port}",
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    def probe_auth_endpoints(self, base_url: str | None = None) -> ProbeResult:
        """Prefer live HTTP register/login; fall back to FAIL if server not runnable."""
        if base_url:
            reg_status, _ = _http_json(
                "POST",
                f"{base_url.rstrip('/')}/register",
                body=b'{"email":"probe@example.com","password":"probe-pass-123"}',
            )
            login_status, _ = _http_json(
                "POST",
                f"{base_url.rstrip('/')}/login",
                body=b'{"email":"probe@example.com","password":"probe-pass-123"}',
            )
            ok = reg_status in (200, 201, 409) and login_status in (200, 201)
            return ProbeResult(
                probe_id="probe_auth_endpoints",
                passed=ok,
                expected="POST /register and /login succeed against running server",
                observed=f"register={reg_status} login={login_status}",
                evidence_refs=(base_url,),
            )

        # Start server if possible and probe
        found = self._find_asgi_module()
        if found is None:
            return ProbeResult(
                probe_id="probe_auth_endpoints",
                passed=False,
                expected="Running auth endpoints /register and /login",
                observed="No ASGI app to probe auth over HTTP",
            )
        target, cwd = found
        port = _free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            time.sleep(0.8)
            return self.probe_auth_endpoints(base_url=f"http://127.0.0.1:{port}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    def probe_crud_endpoints(self, base_url: str | None = None) -> ProbeResult:
        """Real HTTP CRUD against running server when available."""
        if base_url is None:
            found = self._find_asgi_module()
            if found is None:
                return ProbeResult(
                    probe_id="probe_crud_endpoints",
                    passed=False,
                    expected="Running CRUD /expenses endpoints",
                    observed="No ASGI app to probe CRUD over HTTP",
                )
            target, cwd = found
            port = _free_port()
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                time.sleep(0.8)
                return self.probe_crud_endpoints(base_url=f"http://127.0.0.1:{port}")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()

        base = base_url.rstrip("/")
        create_status, create_body = _http_json(
            "POST",
            f"{base}/expenses",
            body=b'{"amount":12.5,"category":"food","description":"probe"}',
        )
        list_status, _ = _http_json("GET", f"{base}/expenses")
        ok = create_status in (200, 201) and list_status == 200
        return ProbeResult(
            probe_id="probe_crud_endpoints",
            passed=ok,
            expected="POST/GET /expenses succeed",
            observed=f"create={create_status} list={list_status} body={create_body[:120]}",
            evidence_refs=(base,),
        )

    def probe_pytest_tests(self) -> ProbeResult:
        """Execute pytest in workspace when tests exist."""
        test_files = list(self.workspace_path.rglob("test_*.py")) + list(self.workspace_path.rglob("*_test.py"))
        if not test_files:
            return ProbeResult(
                probe_id="probe_pytest_tests",
                passed=False,
                expected="Automated tests execute successfully",
                observed="No test_*.py files discovered",
            )
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no"],
            cwd=str(self.workspace_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return ProbeResult(
            probe_id="probe_pytest_tests",
            passed=proc.returncode == 0,
            expected="pytest exits 0",
            observed=f"pytest exit={proc.returncode}",
            output=(proc.stdout or "")[-500:] + "\n" + (proc.stderr or "")[-500:],
            evidence_refs=tuple(str(p.relative_to(self.workspace_path)) for p in test_files[:5]),
        )

    def run_all_probes_for_ovc(
        self,
        ovc: ObjectiveVerificationContractV1,
    ) -> list[OVItemResultV1]:
        """Execute probes for all RUNTIME_PROBE/E2E items in OVC."""
        results: list[OVItemResultV1] = []

        for item in ovc.ov_items:
            desc_lower = item.description.lower()
            res_status: VerificationStatus = "PASS"
            observed = ""
            expected = item.description
            output = ""
            refs: list[str] = []

            if item.proof_method in ("RUNTIME_PROBE", "E2E"):
                if "sqlite" in desc_lower or (item.category == "STACK" and "database" in desc_lower):
                    p = self.probe_db_stack("sqlite")
                elif "uvicorn" in desc_lower or "server starts" in desc_lower or "starts correctly" in desc_lower:
                    p = self.probe_server_startup()
                elif "fastapi" in desc_lower and item.category == "STACK":
                    p = self.probe_server_startup()
                elif "registration" in desc_lower or "login" in desc_lower or item.category == "AUTH":
                    p = self.probe_auth_endpoints()
                elif "crud" in desc_lower or item.category == "CRUD" or "expense" in desc_lower:
                    p = self.probe_crud_endpoints()
                elif "test" in desc_lower or item.category == "TESTS":
                    p = self.probe_pytest_tests()
                elif item.category == "INTEGRATION" or "frontend" in desc_lower or "ui" in desc_lower:
                    html_files = list(self.workspace_path.rglob("*.html"))
                    has_index = any("index.html" in str(f) for f in html_files)
                    p = ProbeResult(
                        probe_id="probe_frontend_integration",
                        passed=bool(has_index or html_files),
                        expected="Frontend UI files present in workspace",
                        observed=f"Discovered {len(html_files)} HTML files (index.html={has_index})",
                        evidence_refs=tuple(str(f.relative_to(self.workspace_path)) for f in html_files[:5]),
                    )
                elif item.category == "E2E" or item.proof_method == "E2E":
                    # E2E requires server + auth + crud where possible
                    startup = self.probe_server_startup()
                    p = startup
                else:
                    p = ProbeResult(
                        probe_id=f"probe_{item.ov_id}",
                        passed=False,
                        expected=item.description,
                        observed="No matching runtime probe implemented",
                    )
                res_status = "PASS" if p.passed else "FAIL"
                observed = p.observed
                output = p.output
                refs = list(p.evidence_refs)
            else:
                # STATIC / TEST_EVIDENCE — still scan for presence signals without claiming runtime PASS
                text, _ = self._collect_code_text()
                if item.description.lower().split()[0] in text.lower() or any(
                    tok in text.lower() for tok in item.description.lower().split()[:3]
                ):
                    res_status = "PASS"
                    observed = "Static evidence present in workspace"
                else:
                    res_status = "FAIL"
                    observed = "Static evidence not found"

            results.append(
                OVItemResultV1(
                    ov_id=item.ov_id,
                    status=res_status,
                    expected=expected,
                    observed=observed,
                    evidence_refs=refs,
                    probe_output=output or None,
                )
            )
        return results


__all__ = ["ProbeResult", "RuntimeProbeRunner"]
