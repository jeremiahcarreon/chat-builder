"""Formats query results compactly for the model."""

import datetime
import decimal

MAX_ROWS = 200
MAX_CELL_CHARS = 200
MAX_TOTAL_CHARS = 15_000


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.6g}" if abs(value) < 1e15 else str(value)
    elif isinstance(value, (decimal.Decimal, datetime.date, datetime.datetime, datetime.time)):
        text = str(value)
    elif isinstance(value, (bytes, bytearray, memoryview)):
        text = f"<{len(bytes(value))} bytes>"
    else:
        text = str(value)
    text = text.replace("\n", " ").replace("|", "/")
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "…"


def format_results(columns: list[str], rows: list[tuple], more: bool) -> str:
    if not columns:
        return "The query returned no columns."
    lines = [" | ".join(columns)]
    total = len(lines[0])
    shown = 0
    for row in rows:
        line = " | ".join(_cell(v) for v in row)
        if total + len(line) > MAX_TOTAL_CHARS:
            more = True
            break
        lines.append(line)
        total += len(line)
        shown += 1
    if shown == 0 and not more:
        lines.append("(no rows)")
    elif more:
        lines.append(
            f"(Showing the first {shown} rows only. Use aggregation, filters or LIMIT for a complete answer.)"
        )
    else:
        lines.append(f"({shown} row{'s' if shown != 1 else ''})")
    return "\n".join(lines)
