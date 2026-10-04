"""Checks run on every incoming message before the main model sees it.

Rate limits and the System 1 classifier arrive in step 6.
"""

from dataclasses import dataclass, field

from app.models import ChatApp, UsageEvent


@dataclass
class Verdict:
    allowed: bool
    reply: str = ""
    meta: dict = field(default_factory=dict)
    usage: list[UsageEvent] = field(default_factory=list)


async def check_message(app: ChatApp, history: list[tuple[str, str]], message: str) -> Verdict:
    return Verdict(allowed=True)
