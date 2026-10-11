#!/usr/bin/env python3
"""A stand-in ERP MCP server for the end-to-end tests.

Serves read-only order, tracking and RMA lookups, plus one tool that changes
data (cancel_order) to check that such tools stay off unless an admin turns
them on. Requires "Authorization: Bearer <token>".

    uv run --with mcp --with uvicorn scripts/mock-erp-mcp.py --port 8931 --token test-token

The endpoint is http://HOST:PORT/mcp.
"""

import argparse
import json

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.server import Context
from mcp_types import ToolAnnotations

ORDERS = {
    "SO-1001": {
        "order_number": "SO-1001", "customer": "Valley Mechanical", "ebay_buyer": "sample_buyer",
        "status": "Shipped", "items": [{"part_no": "EC797", "qty": 1}], "total": 389.00,
    },
    "SO-1002": {
        "order_number": "SO-1002", "customer": "Coastal Air", "ebay_buyer": "coastal_air",
        "status": "Backordered", "items": [{"part_no": "CC161", "qty": 2}], "total": 980.00,
    },
}
TRACKING = {"SO-1001": {"carrier": "UPS", "tracking_number": "1Z999AA10123456784", "status": "Delivered", "delivered_on": "2026-10-03"}}
RMAS = {"sample_buyer": [{"rma": "RMA-517", "order_number": "SO-1001", "status": "Approved", "reason": "Core too thick"}]}

READ_ONLY = ToolAnnotations(read_only_hint=True)
server = MCPServer("Mock ERP")
# Every request's context forwarded by Chat Builder, for the tests to inspect.
seen_context: list = []


def _context(ctx: Context) -> dict:
    data = (ctx.request_context.meta or {}).get("chat-builder/context") or {}
    seen_context.append(data)
    return data


@server.tool(annotations=READ_ONLY, description="Look up an order by its order number (like SO-1001).")
def find_order(order_number: str, ctx: Context) -> str:
    context = _context(ctx)
    order = ORDERS.get(order_number.strip().upper())
    if order is None:
        return f"No order {order_number}."
    buyer = context.get("ebay_buyer")
    if buyer and order["ebay_buyer"] != buyer:
        # The calling system said who's asking: only their own orders.
        return f"Order {order_number} doesn't belong to this buyer."
    return json.dumps(order)


@server.tool(annotations=READ_ONLY, description="Carrier, tracking number and delivery status for an order.")
def get_tracking(order_number: str) -> str:
    tracking = TRACKING.get(order_number.strip().upper())
    return json.dumps(tracking) if tracking else f"No tracking yet for {order_number}."


@server.tool(annotations=READ_ONLY, description="Return requests (RMAs) for an eBay buyer.")
def list_rmas(ebay_buyer: str) -> str:
    return json.dumps(RMAS.get(ebay_buyer.strip(), []))


@server.tool(description="Cancel an order. Changes data.")
def cancel_order(order_number: str) -> str:
    return f"Cancelled {order_number}."


@server.tool(annotations=READ_ONLY, description="Test helper: the context Chat Builder sent with this call.")
def show_context(ctx: Context) -> str:
    return json.dumps({"context": _context(ctx)})


def require_token(app, token: str):
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != f"Bearer {token}":
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await app(scope, receive, send)

    return wrapped


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--token", default="test-token")
    args = parser.parse_args()
    uvicorn.run(require_token(server.streamable_http_app(), args.token), host=args.host, port=args.port, log_level="warning")
