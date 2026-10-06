"""Talk to AI models through LiteLLM (Claude, OpenAI, Gemini, Ollama, ...).

Two slots, chosen in sources.yaml:
- cloud_model: used for docs, code and schema (when the policy allows it)
- local_model: runs on your own machine/server; the only model ever allowed to read data values
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from askcite.config import AiSettings

log = logging.getLogger(__name__)
LOCAL_TIMEOUT_SECONDS = 300  # a local model on a laptop CPU can be slow


class ChatModel(Protocol):
    name: str
    is_local: bool

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: Any = None) -> dict:
        """Return an assistant message dict: {"role": "assistant", "content": ..., "tool_calls": [...]}"""
        ...


@dataclass
class LiteLlmModel:
    name: str
    is_local: bool = False
    api_base: str | None = None
    # Local models get 0 for steadier tool calls. Cloud models keep the provider's default: newer ones
    # (e.g. Gemini 3) are tuned for it and can loop or reason worse with a low temperature.
    temperature: float | None = None
    num_retries: int = 4  # free tiers answer 429 when busy: retry with backoff instead of failing
    # Tried in order when a model is overloaded or rate-limited, instead of making the asker wait
    fallbacks: list[str] = field(default_factory=list)
    timeout_seconds: int = 60
    rest_seconds: int = 120  # skip a busy model for this long, so every step doesn't wait on it again
    _busy_until: dict[str, float] = field(default_factory=dict, repr=False)

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: Any = None) -> dict:
        import litellm

        litellm.drop_params = True  # some providers (e.g. Ollama) reject options like tool_choice
        litellm.suppress_debug_info = True
        busy_errors = (litellm.ServiceUnavailableError, litellm.RateLimitError, litellm.InternalServerError,
                       litellm.APIConnectionError, litellm.Timeout)
        chain = [self.name, *self.fallbacks]
        ready = [m for m in chain[:-1] if self._busy_until.get(m, 0) <= time.monotonic()] + chain[-1:]
        for model in ready[:-1]:
            try:
                return self._complete(litellm, model, messages, tools, tool_choice, num_retries=0)
            except busy_errors as error:
                self._busy_until[model] = time.monotonic() + self.rest_seconds
                log.warning("%s is busy (%s); using the next model for %d s", model, type(error).__name__,
                            self.rest_seconds)
        return self._complete(litellm, ready[-1], messages, tools, tool_choice, num_retries=self.num_retries)

    def _complete(self, litellm, model: str, messages: list[dict], tools: list[dict] | None, tool_choice: Any,
                  num_retries: int) -> dict:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "num_retries": num_retries,
                                  "timeout": self.timeout_seconds}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if self.api_base:
            kwargs["api_base"] = self.api_base
        response = litellm.completion(**kwargs)
        message = response.choices[0].message
        out: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if getattr(message, "tool_calls", None):
            out["tool_calls"] = [
                {"id": call.id, "type": "function",
                 "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"}}
                for call in message.tool_calls
            ]
        return out


def models_from_settings(ai: AiSettings) -> tuple[ChatModel, ChatModel | None]:
    cloud_is_local = ai.cloud_model.startswith("ollama")
    # Fallbacks only for a cloud model: a local model may read data values, so it never hands over to another
    cloud = LiteLlmModel(ai.cloud_model, is_local=cloud_is_local, temperature=0.0 if cloud_is_local else None,
                         fallbacks=[] if cloud_is_local else list(ai.fallback_models))
    if cloud_is_local:
        cloud.timeout_seconds = LOCAL_TIMEOUT_SECONDS
    local = (LiteLlmModel(ai.local_model, is_local=True, api_base=ai.local_api_base, temperature=0.0,
                          timeout_seconds=LOCAL_TIMEOUT_SECONDS) if ai.local_model else None)
    return cloud, local
