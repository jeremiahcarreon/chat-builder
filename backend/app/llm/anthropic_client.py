import json
import logging

import anthropic

from app.llm.base import (
    ChatSession,
    Completion,
    LLMClient,
    LLMError,
    ResetCallback,
    StepResult,
    TextCallback,
    ToolCall,
    ToolResult,
    ToolSpec,
)

log = logging.getLogger(__name__)

# Models that support server-side refusal fallbacks: if the model declines on
# safety grounds, the API re-runs the request on a suitable fallback model.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Retries when the model's streamed tool arguments are not valid JSON.
MAX_JSON_RETRIES = 2


def _api_error(exc: anthropic.APIError) -> LLMError:
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("The Anthropic API key was rejected. Check it in Settings.")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("The selected Claude model was not found. Check the app's model setting.")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("Anthropic rate limit reached. Try again shortly.")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("Could not reach Anthropic.")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"Anthropic returned an error (HTTP {exc.status_code}): {exc.message}")
    return LLMError(str(exc))


def _usage(usage) -> tuple[int, int]:
    input_tokens = (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
    )
    return input_tokens, usage.output_tokens or 0


class AnthropicSession(ChatSession):
    def __init__(self, client: anthropic.AsyncAnthropic, *args, **kwargs):
        self.client = client
        super().__init__(*args, **kwargs)

    def _init_messages(self, history, user_message):
        # Earlier turns are replayed as plain text only (no thinking blocks), so
        # the history never contains blocks bound to an older request.
        self.messages: list[dict] = [{"role": role, "content": text} for role, text in history]
        self.messages.append({"role": "user", "content": user_message})

    def _request(self, tools: list[ToolSpec]) -> tuple[object, dict]:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": self.messages,
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                    "eager_input_streaming": True,
                }
                for t in tools
            ]
        if self.model in FALLBACK_MODELS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
            return self.client.beta.messages, kwargs
        return self.client.messages, kwargs

    async def step(self, tools, on_text: TextCallback, on_reset: ResetCallback) -> StepResult:
        api, kwargs = self._request(tools)
        for attempt in range(MAX_JSON_RETRIES + 1):
            streamed = ""
            try:
                async with api.stream(**kwargs) as stream:
                    async for event in stream:
                        if event.type == "text":
                            streamed += event.text
                            await on_text(event.text)
                    message = await stream.get_final_message()
                break
            except ValueError:
                # Tool arguments the SDK couldn't parse at all; re-issue the turn.
                log.warning("Unparseable tool input from %s (attempt %d)", self.model, attempt + 1)
                if streamed:
                    await on_reset()
                if attempt == MAX_JSON_RETRIES:
                    raise LLMError("The model produced an invalid tool call. Please try again.") from None
            except anthropic.APIError as exc:
                raise _api_error(exc) from exc

        text = "".join(b.text for b in message.content if b.type == "text")
        if not text.startswith(streamed):
            # A server-side fallback replaced partially streamed output.
            await on_reset()
            await on_text(text)

        input_tokens, output_tokens = _usage(message.usage)
        stop = message.stop_reason
        tool_calls = [
            ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else None)
            for b in message.content
            if b.type == "tool_use"
        ]
        self.messages.append({"role": "assistant", "content": message.content})

        if stop == "refusal":
            return StepResult(text, [], "refusal", input_tokens, output_tokens)
        if stop == "max_tokens":
            # A truncated tool input parses as a partial object; never run it.
            return StepResult(text, [], "max_tokens", input_tokens, output_tokens)
        if tool_calls:
            return StepResult(text, tool_calls, "tool_use", input_tokens, output_tokens)
        return StepResult(text, [], "end", input_tokens, output_tokens)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # All results for one assistant turn go back in a single user message.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    def session(self, model, system, history, user_message, max_tokens) -> ChatSession:
        return AnthropicSession(self.client, model, system, history, user_message, max_tokens)

    async def complete(self, model, system, prompt, max_tokens) -> Completion:
        try:
            response = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            raise _api_error(exc) from exc
        if response.stop_reason == "refusal":
            # Treat a refusal of the screening prompt itself as a signal to block.
            return Completion(json.dumps({"verdict": "abuse", "reason": "Model refused"}), 0, 0)
        text = "".join(b.text for b in response.content if b.type == "text")
        input_tokens, output_tokens = _usage(response.usage)
        return Completion(text, input_tokens, output_tokens)
