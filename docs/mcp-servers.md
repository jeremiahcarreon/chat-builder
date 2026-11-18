# Connecting a system over MCP

Chat Builder apps can use tools from any MCP (Model Context Protocol) server,
such as an ERP offering order, tracking and return lookups. This page is for
whoever builds that server.

## What Chat Builder expects

- **Transport:** Streamable HTTP. The address is the MCP endpoint, usually
  ending in `/mcp`, like `https://erp.example.com/mcp`.
- **Authentication:** an optional bearer token, sent on every request as
  `Authorization: Bearer <token>`. Give Chat Builder its own token, scoped to
  the tools it should use.
- **Tools:** listed with `tools/list` whenever an admin adds the server or
  clicks "Re-read tools". Up to 50 tools are read.
- **Read-only hints:** mark lookups with the `readOnlyHint: true` annotation.
  Those are on by default in Chat Builder. Tools without it are treated as
  possibly changing data and stay off until an admin turns them on (and
  confirms). Don't rely on that alone: the token should only reach read-only
  tools.
- **Results:** text content is passed to the model as is (structured content
  is used when there's no text). Up to 20,000 characters per result are kept.
  Return `isError: true` with a readable message for problems the model can
  fix, like an unknown order number.
- **Time limit:** 60 seconds per call.

## Who is asking

Systems calling a chat app's API can say who the message is from:

```json
POST /api/v1/apps/{app}/answer
{"message": "Where is my order?", "thread_id": "ebay-123", "context": {"ebay_buyer": "sample_buyer"}}
```

The context stays with that thread and is sent with every tool call in the
request's `_meta`:

```json
{"method": "tools/call", "params": {"name": "find_order", "arguments": {"order_number": "SO-1001"},
 "_meta": {"chat-builder/context": {"ebay_buyer": "sample_buyer"}}}}
```

Use it to limit results to that person, for example refusing an order that
belongs to a different buyer. Chats from Chat Builder's own pages (staff using
the dashboard or the widget) send no context.

### The signed-in person

When **Tell the server who is asking** is on for the server (the default for
servers added from this version on), chats from signed-in people (admins,
members, and people signed in by an embedding site) also send the person:

```json
"_meta": {"chat-builder/user": {"email": "rep@example.com", "name": "Sam Rep"}}
```

Use it to answer with that person's own permissions rather than the token's,
so the assistant can never show someone more than they could see by signing in
to your system. Refuse when you can't match the email to an account; don't
fall back to the token's identity. Anonymous visitors and API calls send no
person.

## How tools appear to the model

Each tool is offered as `<server name>__<tool name>` (letters, digits, `_`
and `-`; at most 64 characters), with your tool's title and description. Write
descriptions for the model: what the tool returns, and what the arguments look
like ("an order number like SO-1001").

## Testing

`scripts/mock-erp-mcp.py` is a small example server with read-only order,
tracking and return lookups, one tool that changes data, and a buyer check
using the context. The end-to-end tests run against it.
