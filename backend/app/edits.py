"""How much staff changed a draft before sending it.

"Edited" covers everything from deleting a stray word to rewriting the reply, and only the
second says the draft was wrong. Each edit is measured in words, ignoring the staff note
(everything from a "STAFF NOTE:" line on), which drafts include on purpose for staff to read and
remove.

Stored with the decision as "edit": {"changed": share of the draft's words changed (0-1),
"removed_only": staff only deleted text, "size": "tiny" | "light" | "heavy" | "rewrite"}.
"""

import difflib
import re

STAFF_NOTE = re.compile(r"^[ \t>*_#-]*STAFF NOTE\b[*_]*:?[*_]*", re.IGNORECASE | re.MULTILINE)
SIZES = (("tiny", 0.05), ("light", 0.25), ("heavy", 0.6), ("rewrite", float("inf")))
WORD = re.compile(r"\S+")


def split_note(text: str) -> tuple[str, str | None]:
    """(the reply, the staff note after it, if any)."""
    match = STAFF_NOTE.search(text or "")
    if match is None:
        return (text or "").strip(), None
    note = text[match.end():].strip()
    return text[: match.start()].strip(), note or None


def measure(draft: str, final: str) -> dict:
    before = WORD.findall(split_note(draft)[0])
    after = WORD.findall(split_note(final)[0])
    ops = [op for op in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes() if op[0] != "equal"]
    changed = sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops)
    share = min(changed / max(len(before), 1), 1.0)
    size = next(name for name, limit in SIZES if share <= limit)
    return {"changed": round(share, 3), "removed_only": bool(ops) and all(op[0] == "delete" for op in ops), "size": size}


def is_minor(edit: dict | None, limit: float) -> bool:
    """An edit small enough that the draft was right: a few words, or a short cut."""
    if not edit:
        return False
    share = edit.get("changed", 1.0)
    return share <= limit or (edit.get("removed_only", False) and share <= SIZES[1][1])
