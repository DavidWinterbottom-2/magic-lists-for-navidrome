"""Pulling a JSON payload out of whatever a language model actually returned.

Models are asked for bare JSON and mostly comply, but not reliably: replies come
wrapped in ```json fences, prefixed with a sentence of preamble, or carrying
trailing commas and // comments. Every curator needs the same salvage step, so it
lives here once rather than being reimplemented per playlist type.

The extraction is brace-matched rather than regex-matched. The regex this
replaces — `\\{.*?"track_ids".*?\\}` — stopped at the first closing brace, so any
reply containing a nested object (album suggestions, a per-track note) was
truncated mid-structure and failed to parse. The caller then fell back to
play-count ordering and reported "AI service was unavailable", which describes an
outage rather than the parse failure that actually happened.
"""

import re
from typing import Optional

# Prefer an object that looks like a curation reply; a model that prefixes its
# answer with some other JSON shouldn't send us off with the wrong one.
PREFERRED_KEY = '"track_ids"'

ARRAY = re.compile(r"\[([\d\s,]+)\]", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove a leading ```json / ``` and a trailing ```."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def balanced_object_at(text: str, start: int) -> Optional[str]:
    """The complete `{...}` beginning at `start`, or None if it never closes.

    Tracks string state so a brace inside a string value — a reasoning line
    ending "}" say — doesn't close the object early.
    """
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    return None


def find_json_object(text: str) -> Optional[str]:
    """The first complete JSON object in `text`, preferring a curation reply."""
    candidates = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        candidate = balanced_object_at(text, index)
        if not candidate:
            continue
        if PREFERRED_KEY in candidate:
            return candidate
        candidates.append(candidate)
    return candidates[0] if candidates else None


def strip_json_comments(text: str) -> str:
    """Drop // comments and trailing commas, which models emit and JSON forbids."""
    lines = []
    for line in text.split("\n"):
        if "//" in line and "http://" not in line and "https://" not in line:
            line = line[:line.find("//")].rstrip()
        line = re.sub(r",(\s*[\]}])", r"\1", line)
        if line.strip():
            lines.append(line)
    return "\n".join(lines).strip()


def extract_json_payload(content: str) -> str:
    """Best-effort JSON text from a model reply, ready for json.loads.

    Falls back through: a complete object → a bare array of indices (the legacy
    reply format) → the whole cleaned string. The final fallback is deliberate —
    letting json.loads raise on the real content produces a better error than
    guessing here would.
    """
    text = strip_code_fences(content)
    return strip_json_comments(find_json_object(text) or _find_array(text) or text)


def _find_array(text: str) -> Optional[str]:
    match = ARRAY.search(text)
    return match.group(0) if match else None
