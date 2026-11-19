"""Pure data tools: they only transform what the model passes in."""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter

from workflow_core import ToolDefinition

ANALYZE_TREND = ToolDefinition(
    "data.analyze_trend",
    "low",
    description=(
        "Summary statistics (count, mean, median, min, max, change from first to last) "
        "for numbers in JSON: an array of numbers, or objects whose numeric fields are used."
    ),
    input_schema={
        "type": "object",
        "properties": {"data_json": {"type": "string", "minLength": 1, "maxLength": 200000}},
        "required": ["data_json"],
        "additionalProperties": False,
    },
)

KEYWORDS = ToolDefinition(
    "text.keywords",
    "low",
    description="The most frequent words of a text, with counts.",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 200000},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
)

REDACT = ToolDefinition(
    "text.redact",
    "low",
    description="Mask email addresses and phone numbers in a text.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 200000}},
        "required": ["text"],
        "additionalProperties": False,
    },
)

_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]*|[\u4e00-\u9fff]{2,}")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]?\d{4}[-.\s]?\d{4}|\d{3}[-.\s]?\d{3}[-.\s]?\d{4})(?!\d)"
)


def numeric_values(payload) -> list[float]:
    def is_number(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return []
    values = []
    for item in payload:
        if is_number(item):
            values.append(float(item))
        elif isinstance(item, dict):
            values.extend(float(value) for value in item.values() if is_number(value))
    return values


def analyze_trend(context, tool, args) -> dict:
    try:
        payload = json.loads(args["data_json"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"data_json is not valid JSON: {exc}") from exc
    values = numeric_values(payload)
    if not values:
        raise ValueError("No numbers found in data_json.")
    first, last = values[0], values[-1]
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "min": min(values),
        "max": max(values),
        "change": round(last - first, 4),
        "change_percent": round((last - first) / abs(first) * 100, 2) if first else None,
    }


def keywords(context, tool, args) -> dict:
    words = _WORD.findall(args["text"].lower())
    if not words:
        raise ValueError("No words found in text.")
    ranking = Counter(words).most_common(args.get("top_k", 8))
    return {"keywords": [{"keyword": word, "count": count} for word, count in ranking]}


def redact(context, tool, args) -> dict:
    masked = _EMAIL.sub(r"\1***\2", args["text"])
    masked = _PHONE.sub(lambda match: "***" + match.group()[-4:], masked)
    return {"text": masked}


TOOLS = [(ANALYZE_TREND, analyze_trend), (KEYWORDS, keywords), (REDACT, redact)]
