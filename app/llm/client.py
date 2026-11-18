"""Thin wrapper around the OpenAI-compatible chat API.

One client covers Gemini, Groq, OpenRouter and Ollama. FakeLLM returns scripted answers
so tests and CI never need an API key.
"""

import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.llm.usage import Usage, UsageTracker

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The assistant message exactly as the API returned it. Gemini 3 attaches a
    # thought signature to each tool call and rejects the next turn (400) unless
    # it is sent back untouched, so the history reuses this instead of rebuilding.
    raw: dict | None = None

    def assistant_message(self) -> dict:
        """The message to append to the history so the next turn sees this reply."""
        if self.raw:
            return self.raw
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        return msg


class LLMError(RuntimeError):
    pass


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        tracker: UsageTracker | None = None,
        temperature: float = 0.1,
        max_retries: int = 4,
        fallback_models: list[str] | None = None,
    ):
        from openai import OpenAI

        # Ollama ignores the key but the SDK requires a non-empty one.
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=0)
        self.model = model
        self.fallback_models = list(fallback_models or [])
        self.temperature = temperature
        self.max_retries = max_retries
        self.tracker = tracker

    def _create(self, model, kwargs, RateLimitError, APIConnectionError, APIStatusError):
        """One model, with backoff. Raises LLMError(unavailable=True) on 429/5xx/network."""
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            start = time.perf_counter()
            try:
                resp = self._client.chat.completions.create(model=model, **kwargs)
                return resp, int((time.perf_counter() - start) * 1000)
            except (RateLimitError, APIConnectionError) as e:
                # Free tiers return 429 often: back off and retry.
                if attempt == self.max_retries:
                    err = LLMError(f"LLM unavailable after {attempt + 1} attempts: {e}")
                    err.unavailable = True
                    raise err from e
                log.warning("LLM %s, retrying in %.0fs", type(e).__name__, delay)
            except APIStatusError as e:
                if e.status_code < 500:
                    detail = e.body if e.body is not None else e.message
                    raise LLMError(f"LLM error {e.status_code}: {str(detail)[:500]}") from e
                if attempt == self.max_retries:
                    err = LLMError(f"LLM error {e.status_code}: {e.message}")
                    err.unavailable = True
                    raise err from e
                log.warning("LLM %s returned %d, retrying in %.0fs", model, e.status_code, delay)
            time.sleep(delay)
            delay *= 2
        raise LLMError("unreachable")

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        operation: str = "chat",
    ) -> ChatResult:
        from openai import APIConnectionError, APIStatusError, RateLimitError

        kwargs: dict = {"messages": messages, "temperature": self.temperature}
        if tools:
            kwargs["tools"] = tools
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        models = [self.model] + [m for m in self.fallback_models if m != self.model]
        for n, model in enumerate(models):
            try:
                resp, latency = self._create(model, kwargs, RateLimitError, APIConnectionError,
                                             APIStatusError)
                break
            except LLMError as e:
                if not getattr(e, "unavailable", False) or n == len(models) - 1:
                    raise
                log.warning("LLM %s unavailable, falling back to %s", model, models[n + 1])
        # Stick with the model that answered, so later calls skip the overloaded one.
        self.model = model
        if self.tracker and resp.usage:
            self.tracker.record(
                Usage(
                    model=self.model,
                    operation=operation,
                    prompt_tokens=resp.usage.prompt_tokens or 0,
                    completion_tokens=resp.usage.completion_tokens or 0,
                    latency_ms=latency,
                )
            )

        msg = resp.choices[0].message
        calls = []
        for i, tc in enumerate(msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id or f"call_{i}", name=tc.function.name, arguments=args))
        raw = None
        if calls:
            raw = msg.model_dump(exclude_none=True)
            raw["role"] = "assistant"
            raw.setdefault("content", "")
        return ChatResult(content=msg.content, tool_calls=calls, raw=raw)


class FakeLLM:
    """Returns pre-scripted ChatResults in order. Records the messages it received."""

    def __init__(self, script: Iterable[ChatResult | str] = ()):
        self._script = [r if isinstance(r, ChatResult) else ChatResult(content=r) for r in script]
        self.calls: list[dict] = []
        self.model = "fake"

    def chat(self, messages, tools=None, json_mode=False, operation="chat") -> ChatResult:
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})
        if not self._script:
            return ChatResult(content="(fake LLM: no scripted answer left)")
        return self._script.pop(0)
