from __future__ import annotations

import json
from pathlib import Path

from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState

STATE_ROOT = Path(__file__).resolve().parent.parent / "reports" / "aix_orchestrator_v2"


class AixOrchestratorV2StateStore:
    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root or STATE_ROOT
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, state: AixOrchestratorV2RunState) -> str:
        task_id = str(state.task_id or "unknown")
        task_dir = self._root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / f"{state.run_id}.json"
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        latest = task_dir / "latest.json"
        latest.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return str(path)

    def get_by_run_id(self, run_id: str) -> AixOrchestratorV2RunState | None:
        for candidate in self._root.glob(f"**/{run_id}.json"):
            return AixOrchestratorV2RunState.model_validate_json(candidate.read_text(encoding="utf-8"))
        return None

    def get_latest_by_task_id(self, task_id: str) -> AixOrchestratorV2RunState | None:
        path = self._root / str(task_id) / "latest.json"
        if not path.exists():
            return None
        return AixOrchestratorV2RunState.model_validate_json(path.read_text(encoding="utf-8"))

    def get_latest_by_task_number(self, task_number: int) -> AixOrchestratorV2RunState | None:
        for candidate in self._root.glob("**/latest.json"):
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            task_payload = payload.get("task_payload") or {}
            if int(task_payload.get("task_number") or 0) == int(task_number):
                return AixOrchestratorV2RunState.model_validate(payload)
        return None


state_store = AixOrchestratorV2StateStore()
