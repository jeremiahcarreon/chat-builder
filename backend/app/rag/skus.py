"""SKU tags on documents (for example install instructions for one relay kit).

Tags are matched loosely: case, spaces and dashes are ignored, so "rk2",
"RK-2" and "rk 2" are the same SKU.
"""

import re

# Part-number-like tokens: letters and digits mixed, optionally with dashes
# (CC161, RK-2, FS161-2X12, FK-12). Requires at least one letter and one digit.
PART_NUMBER = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
# Common words that look like part numbers but aren't (sizes, years, model years).
NOT_PARTS = re.compile(r"^(\d+(in|mm|cm|ft|v|w|a|hp|x\d+|th|st|nd|rd|s)|v\d+|\d{4}s?|q[1-4]|h[12]|mp[34]|x\d+)$", re.IGNORECASE)


def key(sku: str) -> str:
    """The normalized form used for matching."""
    return re.sub(r"[^A-Z0-9]", "", sku.upper())


def find_skus(text: str) -> list[str]:
    """Part-number-like tokens in text, in order, without duplicates."""
    found = []
    for match in PART_NUMBER.finditer(text):
        token = match.group(0).strip("-")
        if len(key(token)) < 3 or NOT_PARTS.match(token):
            continue
        found.append(token.upper())
    return list(dict.fromkeys(found))


def from_filename(filename: str) -> list[str]:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename)
    return find_skus(re.sub(r"[_]+", " ", stem))
