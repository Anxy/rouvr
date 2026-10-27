"""AIX Model Registry — ModelProfileV1 (config-driven from models.json)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

ProviderKind = Literal["openai", "deepseek", "qwen"]


class ModelProfileV1(BaseModel):
    model_id: str
    provider: ProviderKind
    family: str
    display_name: str = ""
    context_window: int = 128000
    input_cost_per_M: float = Field(ge=0.0)
    output_cost_per_M: float = Field(ge=0.0)
    cached_cost_per_M: float = Field(default=0.0, ge=0.0)
    capabilities: tuple[str, ...] = ()
    max_tokens: int = 8192
    supports_tools: bool = True
    is_active: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_per_1k_input_tokens(self) -> float:
        return round(self.input_cost_per_M / 1000.0, 8)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_per_1k_output_tokens(self) -> float:
        return round(self.output_cost_per_M / 1000.0, 8)


__all__ = [
    "ProviderKind",
    "ModelProfileV1",
]
