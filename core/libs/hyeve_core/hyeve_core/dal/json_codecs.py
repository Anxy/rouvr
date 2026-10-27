from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import asyncpg


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return str(value)


def dumps_json(value: Any) -> str:
    return json.dumps(to_jsonable(value))


async def register_asyncpg_json_codecs(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("json", encoder=dumps_json, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("jsonb", encoder=dumps_json, decoder=json.loads, schema="pg_catalog")
