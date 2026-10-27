from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog


def resolve_dal_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "hyeve_aix_service" / "dal_config.json"


def resolve_effective_dal_config_path() -> Path:
    config_path = resolve_dal_config_path()
    database_url = os.getenv("HYEVE_AIX_SERVICE_DATABASE_URL") or os.getenv("DATABASE_URL")
    redis_url = os.getenv("HYEVE_AIX_SERVICE_REDIS_URL") or os.getenv("REDIS_URL")
    if not database_url and not redis_url:
        return config_path

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if database_url:
        config.setdefault("database", {}).setdefault("primary", {})["connection_string"] = database_url
    if redis_url:
        config.setdefault("cache", {})["redis_url"] = redis_url

    effective_path = Path(tempfile.gettempdir()) / f"hyeve_aix_service_dal_config_{os.getpid()}.json"
    effective_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    return effective_path


async def initialise_dal(*, logger_name: str) -> dict[str, Any]:
    logger = structlog.get_logger(logger_name)
    source_config_path = resolve_dal_config_path()
    config_path = resolve_effective_dal_config_path()
    status: dict[str, Any] = {
        "ready": False,
        "config_path": str(config_path),
        "source_config_path": str(source_config_path),
        "runtime_overrides_applied": config_path != source_config_path,
        "error": None,
    }
    try:
        from hyeve_core.dal.dal_config import DALConfig
        from hyeve_core.dal.dal_service import init_dal_service

        dal_config = DALConfig.load_from_file(str(config_path))
        await init_dal_service(dal_config)
        status["ready"] = True
        logger.info("dal.initialised", config_path=str(config_path))
    except Exception as exc:
        status["error"] = str(exc)
        logger.warning("dal.initialisation_failed", error=str(exc), config_path=str(config_path))
    return status
