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
