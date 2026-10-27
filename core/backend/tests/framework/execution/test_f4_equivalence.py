"""F4 equivalence — execution port ON/OFF must not invent a second execute SoR.

Additive check: both paths ultimately target ``run_real_execute`` semantics.
Flag ``AIX_FRAMEWORK_EXECUTION_PORT`` remains default false.
"""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.framework.execution.real_execute_adapter import RealExecuteAdapter
from backend.hyeve_aix_service.config import settings


def test_execution_port_flag_defaults_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_EXECUTION_PORT", True)) is False


def test_real_execute_adapter_delegates_to_run_real_execute() -> None:
    src = Path(RealExecuteAdapter.__module__.replace(".", "/") + ".py")
    # Resolve from package file
    import backend.aix_core.framework.execution.real_execute_adapter as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "run_real_execute" in text
    assert "class RealExecuteAdapter" in text


def test_no_second_executor_in_framework_execution() -> None:
    import backend.aix_core.framework.execution as pkg

    root = Path(pkg.__file__).resolve().parent
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "openai.OpenAI" not in text
        assert "direct_provider" not in text
