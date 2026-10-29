import json

import pytest

from backend.rouvr_models.providers import get_provider


def test_default_is_stub_and_offline():
    p = get_provider(env={})
    assert p.name == "stub"
    assert json.loads(p.chat([{"role": "user", "content": "hi"}]))["echo"] == "hi"


def test_meta_requires_key_from_env_only():
    with pytest.raises(RuntimeError, match="META_MODEL_API_KEY"):
        get_provider("meta", env={})
    env = {"META_MODEL_API_KEY": "fake-test-key",
           "META_MODEL_API_BASE_URL": "https://models.example.invalid",
           "META_MODEL_NAME": "test-model"}
    p = get_provider("meta", env=env)
    assert p.api_key == "fake-test-key" and p.base_url.startswith("https://")
    with pytest.raises(RuntimeError, match="META_MODEL_API_BASE_URL"):
        get_provider("meta", env={"META_MODEL_API_KEY": "fake-test-key"})


def test_ollama_needs_no_key_and_is_overridable():
    p = get_provider(env={"ROUVR_MODEL_PROVIDER": "ollama", "OLLAMA_MODEL": "llama3.2"})
    assert p.api_key is None and p.model == "llama3.2"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        get_provider("nope", env={})
