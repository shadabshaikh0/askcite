"""Talk to AI models through LiteLLM (Claude, OpenAI, Gemini, Ollama, ...).

Two slots, chosen in sources.yaml:
- cloud_model: used for docs, code and schema (when the policy allows it)
- local_model: runs on your own machine/server; the only model ever allowed to read data values
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from askcite.config import AiSettings


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

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: Any = None) -> dict:
        import litellm

        litellm.drop_params = True  # some providers (e.g. Ollama) reject options like tool_choice
        litellm.suppress_debug_info = True
        kwargs: dict[str, Any] = {"model": self.name, "messages": messages, "num_retries": self.num_retries}
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
    cloud = LiteLlmModel(ai.cloud_model, is_local=cloud_is_local, temperature=0.0 if cloud_is_local else None)
    local = (LiteLlmModel(ai.local_model, is_local=True, api_base=ai.local_api_base, temperature=0.0)
             if ai.local_model else None)
    return cloud, local
