"""Provider-neutral interface for one chat exchange with tool calling.

Each provider keeps the conversation in its own native message format inside a
ChatSession, so provider-specific details (Anthropic content blocks, OpenAI
tool_calls) are passed back exactly as the provider returned them.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.llm.sampling import Sampling

TextCallback = Callable[[str], Awaitable[None]]
ResetCallback = Callable[[], Awaitable[None]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the model sent unparseable arguments


@dataclass
class ToolResult:
    call: ToolCall
    content: str
    is_error: bool = False


StopReason = Literal["end", "tool_use", "max_tokens", "refusal"]


@dataclass
class StepResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: StopReason = "end"
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class ChatSession(ABC):
    """One user turn: the system prompt, prior turns as text, and the new user message."""

    def __init__(
        self,
        model: str,
        system: str,
        history: list[tuple[str, str]],
        user_message: str,
        max_tokens: int,
        sampling: "Sampling | None" = None,
    ):
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.sampling = sampling
        self._init_messages(history, user_message)

    @abstractmethod
    def _init_messages(self, history: list[tuple[str, str]], user_message: str) -> None: ...

    @abstractmethod
    async def step(
        self, tools: list[ToolSpec], on_text: TextCallback, on_reset: ResetCallback
    ) -> StepResult:
        """Streams one model response and appends it to the conversation."""

    @abstractmethod
    def add_tool_results(self, results: list[ToolResult]) -> None: ...

    def retry_empty(self) -> bool:
        """After an empty reply (no text, no tool calls), prepare one retry. False if unsupported."""
        return False


class LLMClient(ABC):
    @abstractmethod
    def session(
        self,
        model: str,
        system: str,
        history: list[tuple[str, str]],
        user_message: str,
        max_tokens: int,
        sampling: "Sampling | None" = None,
    ) -> ChatSession: ...

    @abstractmethod
    async def complete(
        self, model: str, system: str, prompt: str, max_tokens: int, sampling: "Sampling | None" = None
    ) -> Completion:
        """A single non-streaming response with no tools (used by the classifier)."""


class LLMError(Exception):
    """A user-facing error from the model provider."""
