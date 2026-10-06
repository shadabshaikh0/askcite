"""Model settings: what is sent to local vs cloud models."""

import importlib.util

from askcite.config import AiSettings
from askcite.llm import models_from_settings


def test_retry_support_is_installed():
    # litellm imports tenacity only when num_retries is set; without it every real model call fails
    assert importlib.util.find_spec("tenacity") is not None


def test_only_local_models_get_a_fixed_temperature():
    cloud, local = models_from_settings(AiSettings(cloud_model="gemini/gemini-flash-latest",
                                                   local_model="ollama_chat/qwen2.5:7b"))
    assert cloud.temperature is None and not cloud.is_local
    assert local.temperature == 0.0 and local.is_local
    ollama_only, _ = models_from_settings(AiSettings(cloud_model="ollama_chat/qwen2.5:7b"))
    assert ollama_only.temperature == 0.0 and ollama_only.is_local


class FakeResponse:
    def __init__(self, text):
        message = type("Message", (), {"content": text, "tool_calls": None})()
        self.choices = [type("Choice", (), {"message": message})()]


def test_a_busy_model_hands_over_to_the_fallback_and_rests(monkeypatch):
    import litellm

    calls = []

    def completion(**kwargs):
        calls.append((kwargs["model"], kwargs["num_retries"], kwargs["timeout"]))
        if kwargs["model"] == "gemini/busy":
            raise litellm.ServiceUnavailableError("high demand", llm_provider="gemini", model="busy")
        return FakeResponse("hello")

    monkeypatch.setattr(litellm, "completion", completion)
    model, _ = models_from_settings(AiSettings(cloud_model="gemini/busy", fallback_models=["gemini/spare"]))
    assert model.chat([{"role": "user", "content": "hi"}])["content"] == "hello"
    assert calls == [("gemini/busy", 0, 60), ("gemini/spare", 4, 60)]  # no waiting on the busy one
    model.chat([{"role": "user", "content": "again"}])
    assert [c[0] for c in calls[2:]] == ["gemini/spare"]  # the busy model rests for a while


def test_a_local_model_never_falls_back_to_another():
    model, _ = models_from_settings(AiSettings(cloud_model="ollama_chat/qwen2.5:7b", fallback_models=["gemini/x"]))
    assert model.fallbacks == [] and model.timeout_seconds == 300


def test_fallback_models_from_the_environment(monkeypatch, tmp_path):
    from askcite.config import get_settings

    monkeypatch.setenv("ASKCITE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ASKCITE_FALLBACK_MODELS", "gemini/a, gemini/b")
    get_settings.cache_clear()
    try:
        assert get_settings().sources.ai.fallback_models == ["gemini/a", "gemini/b"]
    finally:
        get_settings.cache_clear()
