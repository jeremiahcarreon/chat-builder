"""OpenAI, Google Gemini and local Ollama models, all through OpenAI-compatible chat completions."""

import json
import logging
import uuid

import openai

from app.llm.base import (
    ChatSession,
    Completion,
    LLMClient,
    LLMError,
    StepResult,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from app.llm.sampling import Sampling, request_params
from app.providers import PROVIDERS, openai_compatible_client

log = logging.getLogger(__name__)

EMPTY_REPLY_NUDGE = (
    "Your previous reply was empty. Answer the question. If you need data, call sql_query "
    'with exactly two string arguments, for example {"source_id": "<source_id>", "sql": '
    '"SELECT ..."}. Keep the SQL on one line and put single quotes around text values.'
)


def _api_error(provider: str, exc: openai.APIError) -> LLMError:
    label = PROVIDERS[provider].label
    if isinstance(exc, openai.AuthenticationError):
        return LLMError(f"The {label} API key was rejected. Check it in Settings.")
    if isinstance(exc, openai.NotFoundError):
        return LLMError(f"The selected {label} model was not found. Check the app's model setting.")
    if isinstance(exc, openai.RateLimitError):
        return LLMError(f"{label} rate limit reached. Try again shortly.")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(f"Could not reach {label}.")
    if isinstance(exc, openai.APIStatusError):
        return LLMError(f"{label} returned an error (HTTP {exc.status_code}): {exc.message}")
    return LLMError(str(exc))


class OpenAISession(ChatSession):
    def __init__(self, client: openai.AsyncOpenAI, provider: str, *args, **kwargs):
        self.client = client
        self.provider = provider
        super().__init__(*args, **kwargs)

    def _init_messages(self, history, user_message):
        self.messages: list[dict] = [{"role": "system", "content": self.system}]
        self.messages += [{"role": role, "content": text} for role, text in history]
        self.messages.append({"role": "user", "content": user_message})

    def _limit_kwargs(self) -> dict:
        # OpenAI's newer models only accept max_completion_tokens.
        if self.provider == "openai":
            return {"max_completion_tokens": self.max_tokens}
        return {"max_tokens": self.max_tokens}

    async def step(self, tools, on_text, on_reset) -> StepResult:
        kwargs: dict = {
            "model": self.model,
            "messages": self.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._limit_kwargs(),
            **request_params(self.provider, self.model, self.sampling),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]

        text = ""
        calls: dict[int, dict] = {}
        finish_reason = None
        input_tokens = output_tokens = 0
        try:
            try:
                stream = await self.client.chat.completions.create(**kwargs)
            except openai.BadRequestError as exc:
                if not (self.sampling and self.sampling.effort and "reasoning_effort" in kwargs and "effort" in str(exc.message).lower()):
                    raise
                # The tier named an effort level this model lacks; answer without it.
                log.warning("%s rejected effort %r; answering without it", self.model, self.sampling.effort)
                self.sampling = Sampling(self.sampling.temperature, self.sampling.seed)
                kwargs.pop("reasoning_effort")
                stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens or 0
                    output_tokens = chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    await on_text(delta.content)
                for tc in delta.tool_calls or []:
                    entry = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": "", "extra": None})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["arguments"] += tc.function.arguments
                    # Gemini returns thought signatures that must be sent back unchanged.
                    extra = (tc.model_extra or {}).get("extra_content")
                    if extra:
                        entry["extra"] = extra
        except openai.APIError as exc:
            raise _api_error(self.provider, exc) from exc

        tool_calls: list[ToolCall] = []
        native_calls = []
        for index in sorted(calls):
            entry = calls[index]
            call_id = entry["id"] or f"call_{uuid.uuid4().hex[:12]}"
            try:
                arguments = json.loads(entry["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    arguments = None
            except json.JSONDecodeError:
                arguments = None
            tool_calls.append(ToolCall(call_id, entry["name"], arguments))
            native = {
                "id": call_id,
                "type": "function",
                "function": {"name": entry["name"], "arguments": entry["arguments"] or "{}"},
            }
            if entry["extra"]:
                native["extra_content"] = entry["extra"]
            native_calls.append(native)

        # Content may be null only alongside tool calls; Ollama rejects a null-content message.
        assistant: dict = {"role": "assistant", "content": text or (None if native_calls else "")}
        if native_calls:
            assistant["tool_calls"] = native_calls
        self.messages.append(assistant)

        if finish_reason == "length":
            return StepResult(text, [], "max_tokens", input_tokens, output_tokens)
        if finish_reason == "content_filter":
            return StepResult(text, [], "refusal", input_tokens, output_tokens)
        if tool_calls:
            return StepResult(text, tool_calls, "tool_use", input_tokens, output_tokens)
        return StepResult(text, [], "end", input_tokens, output_tokens)

    def retry_empty(self) -> bool:
        # Some local models emit a malformed tool call that the server discards, leaving
        # an empty reply. Drop it and ask once more.
        if self.messages and self.messages[-1].get("role") == "assistant" and not self.messages[-1].get("content"):
            self.messages.pop()
        self.messages.append({"role": "user", "content": EMPTY_REPLY_NUDGE})
        return True

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r.call.id, "content": r.content})


class OpenAICompatibleClient(LLMClient):
    def __init__(self, provider: str, api_key: str):
        self.provider = provider
        self.client = openai_compatible_client(provider, api_key)

    def session(self, model, system, history, user_message, max_tokens, sampling=None) -> ChatSession:
        return OpenAISession(self.client, self.provider, model, system, history, user_message, max_tokens, sampling)

    async def complete(self, model, system, prompt, max_tokens, sampling=None) -> Completion:
        limit = {"max_completion_tokens": max_tokens} if self.provider == "openai" else {"max_tokens": max_tokens}
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                **limit,
                **request_params(self.provider, model, sampling),
            )
        except openai.APIError as exc:
            raise _api_error(self.provider, exc) from exc
        usage = response.usage
        return Completion(
            response.choices[0].message.content or "",
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )
