"""MCP servers as a data source: tools another system (like the ERP) offers.

An admin connects a server by URL, with an optional bearer token. Processing the
source lists its tools; the admin picks which ones the app may use. Tools the
server marks read-only are on by default; tools that may change data start off.

During a chat, the allowed tools are offered to the model next to sql_query,
named "<source>__<tool>". Calls send the calling system's context (like the eBay
buyer from the API's "context" field) in the request's _meta under
"chat-builder/context", so the server can limit what it returns. When the admin
turns on "send_person", calls also carry the signed-in person under
"chat-builder/user" ({"email", "name"}) so the server can apply that person's own
permissions instead of the token's.

Config: {"url", "tools": [{name, title, description, input_schema, read_only, destructive}],
"enabled": [tool names], "risk": {tool name: "read" | "reversible" | "irreversible"}};
the token is the source's encrypted secret. "risk" holds the admin's overrides of what the
server's hints say a tool can do (see app/gate.py).
"""

import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from app.crypto import decrypt
from app.models import DataSource

log = logging.getLogger(__name__)

MCP_KIND = "mcp"
CONTEXT_META_KEY = "chat-builder/context"
PERSON_META_KEY = "chat-builder/user"
CONNECT_TIMEOUT = 15
CALL_TIMEOUT = 60
MAX_RESULT_CHARS = 20_000
MAX_TOOLS = 50
# Tool names sent to model providers: letters, digits, _ and -, at most 64 characters.
TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]+")


class McpFailed(Exception):
    """A readable problem talking to an MCP server."""


@dataclass
class McpTool:
    source_id: str
    source_name: str
    name: str
    title: str
    description: str
    input_schema: dict
    # What calling it can do: "read", "reversible" (changes data that can be put back) or
    # "irreversible". The review gate holds answers that used an irreversible tool.
    risk: str = "irreversible"

    @property
    def model_name(self) -> str:
        """The name the model sees: unique per source, within providers' limits."""
        prefix = TOOL_NAME.sub("_", self.source_name.lower()).strip("_")[:24] or "tools"
        return f"{prefix}__{TOOL_NAME.sub('_', self.name)}"[:64]


def secret_context(source_id: uuid.UUID) -> str:
    return f"mcp_token:{source_id}"


def token_for(source: DataSource) -> str | None:
    if not source.encrypted_secret:
        return None
    return decrypt(source.encrypted_secret, secret_context(source.id))


def _leaves(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


def _friendly(exc: BaseException, url: str, statuses: list[int]) -> str:
    if 401 in statuses or 403 in statuses:
        return "The MCP server rejected the token."
    if 404 in statuses:
        return f"Nothing answered at {url}. Check the address (it usually ends in /mcp)."
    if 421 in statuses:
        return (
            "The MCP server refused the host name in this address (\"Invalid Host header\"). Add it to the "
            "server's allowed hosts, or turn off its localhost-only DNS rebinding protection."
        )
    for leaf in _leaves(exc):
        if isinstance(leaf, httpx2.ConnectError):
            return f"Couldn't reach the MCP server at {url}."
        if isinstance(leaf, httpx2.TimeoutException):
            return "The MCP server took too long to answer."
    first = next(_leaves(exc), exc)
    return f"The MCP server returned an error: {str(first)[:300] or type(first).__name__}"


@asynccontextmanager
async def _client(url: str, token: str | None, timeout: float):
    statuses: list[int] = []

    async def record(response) -> None:
        statuses.append(response.status_code)

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx2.AsyncClient(headers=headers, timeout=timeout, event_hooks={"response": [record]})
    try:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            yield client
    except McpFailed:
        raise
    except BaseException as exc:  # noqa: BLE001 - anyio wraps transport errors in groups
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        raise McpFailed(_friendly(exc, url, statuses)) from exc
    finally:
        await http.aclose()


async def list_tools(url: str, token: str | None) -> list[dict]:
    """The server's tools, as stored in the source's config."""
    async with _client(url, token, CONNECT_TIMEOUT) as client:
        tools, cursor = [], None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor or len(tools) >= MAX_TOOLS:
                break
    return [
        {
            "name": tool.name,
            "title": tool.title or (tool.annotations.title if tool.annotations else None) or tool.name,
            "description": (tool.description or "").strip()[:2000],
            "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            # Only an explicit read-only hint counts; MCP's default is "may change data".
            "read_only": bool(tool.annotations and tool.annotations.read_only_hint),
            # MCP's default for a tool that isn't read-only is "may destroy data"; only an
            # explicit destructiveHint of false marks its changes as safe to undo.
            "destructive": not (tool.annotations and tool.annotations.destructive_hint is False),
        }
        for tool in tools[:MAX_TOOLS]
    ]


def default_enabled(tools: list[dict]) -> list[str]:
    return [t["name"] for t in tools if t["read_only"]]


def result_text(result) -> str:
    parts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    if not parts and result.structured_content is not None:
        parts = [json.dumps(result.structured_content, default=str)]
    text = "\n".join(parts).strip() or "(no content)"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n... (truncated)"
    return text


def call_meta(context: dict | None, person: dict | None) -> dict | None:
    """The tools/call _meta: the calling system's context, and the signed-in person if any."""
    meta = {}
    if context:
        meta[CONTEXT_META_KEY] = context
    if person and person.get("email"):
        meta[PERSON_META_KEY] = {"email": person["email"], "name": person.get("name") or ""}
    return meta or None


async def call_tool(
    url: str, token: str | None, name: str, arguments: dict, context: dict | None, person: dict | None = None
) -> tuple[str, bool]:
    """(result text, is_error) for one tool call."""
    meta = call_meta(context, person)
    async with _client(url, token, CALL_TIMEOUT) as client:
        result = await client.call_tool(name, arguments, meta=meta)
    return result_text(result), bool(result.is_error)


async def index_mcp(source: DataSource) -> dict:
    """Lists the server's tools. A re-read keeps the admin's choices and turns on new read-only tools."""
    from app.ingest import IngestError

    try:
        tools = await list_tools(source.config.get("url", ""), token_for(source))
    except McpFailed as exc:
        raise IngestError(str(exc)) from exc
    if not tools:
        raise IngestError("Connected, but the server doesn't offer any tools.")
    names = {t["name"] for t in tools}
    previous = source.config.get("tools")
    imported = source.config.get("imported_enabled")
    if imported is not None:
        # An imported app keeps the choices it was exported with.
        enabled = [n for n in imported if n in names]
    elif previous is None:
        enabled = default_enabled(tools)
    else:
        # Tools that are new, or newly marked read-only by the server, turn on like on a first read.
        was_read_only = {t["name"]: t.get("read_only", False) for t in previous}
        enabled = [n for n in source.config.get("enabled", []) if n in names]
        enabled += [
            t["name"] for t in tools
            if t["read_only"] and not was_read_only.get(t["name"], False) and t["name"] not in enabled
        ]
    return {
        "tools": tools, "enabled": enabled, "tool_count": len(tools), "enabled_count": len(enabled), "imported_enabled": None,
    }


RISKS = ("read", "reversible", "irreversible")


def tool_risk(tool: dict, overrides: dict | None = None) -> str:
    """What calling a tool can do: the admin's override, else the server's hints."""
    override = (overrides or {}).get(tool["name"])
    if override in RISKS:
        return override
    if tool.get("read_only"):
        return "read"
    return "irreversible" if tool.get("destructive", True) else "reversible"


def enabled_tools(source_id: uuid.UUID, source_name: str, config: dict) -> list[McpTool]:
    enabled = set(config.get("enabled", []))
    overrides = config.get("risk") or {}
    return [
        McpTool(
            str(source_id), source_name, t["name"], t.get("title") or t["name"], t.get("description", ""),
            t["input_schema"], tool_risk(t, overrides),
        )
        for t in config.get("tools", [])
        if t["name"] in enabled
    ]
