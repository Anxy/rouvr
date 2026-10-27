import pytest
from backend.rouvr_models.providers import get_provider
from workers.meta_worker.worker import MetaWorker


def test_requires_key_from_env_only(monkeypatch):
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="META_MODEL_API_KEY"):
        MetaWorker()


def test_roles_with_stub_provider():
    w = MetaWorker(get_provider("stub"))
    assert "stub" in w.run("compile", "send the morning report")
    with pytest.raises(ValueError):
        w.run("nope", "x")
