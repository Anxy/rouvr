from __future__ import annotations

from pathlib import Path

FORBIDDEN_PATTERNS = [
    "datetime.now(",
    "time.time(",
    "random.",
    "uuid.uuid4(",
    "os.urandom(",
    "httpx.",
    "requests.",
    "openai.",
]


def test_temporal_workflow_files_avoid_nondeterministic_apis() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow_dir = root / "workflows" / "temporal"
    if not workflow_dir.exists():
        # Guardrail is forward-compatible: enforcement activates once temporal workflow files are added.
        return

    offenders: list[str] = []
    for path in workflow_dir.rglob("*.py"):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in content:
                offenders.append(f"{path.as_posix()} -> {pattern}")
    assert not offenders, "Temporal determinism violations:\n" + "\n".join(offenders)
