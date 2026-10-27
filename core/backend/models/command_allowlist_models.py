from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

COMMAND_ALLOWLIST_SCHEMA_VERSION_Current = "command_allowlist.current"
AllowlistMatchMode = Literal["exact", "prefix"]


class AllowlistRuleCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AllowlistMatchMode
    argv: list[str] = Field(..., min_length=1)


class CommandAllowlistProfileCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["command_allowlist.current"]
    profile_id: str = Field(..., min_length=1)
    rules: list[AllowlistRuleCurrent] = Field(..., min_length=1)


def validate_command_allowlist_profile(payload: dict) -> CommandAllowlistProfileCurrent:
    return CommandAllowlistProfileCurrent.model_validate(payload)
