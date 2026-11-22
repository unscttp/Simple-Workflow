"""Tool schemas in the JSON Schema subset Gemini accepts.

Gemini reads function parameters as its OpenAPI-style ``Schema`` and rejects
the whole request (HTTP 400) when a tool uses anything outside it. The tools of
this app and of NLG use plain JSON Schema, so before a request to Gemini:

* ``$ref`` is replaced by the definition it points to (recursion is cut after
  a few levels and becomes a plain object);
* a type list such as ``["string", "null"]`` becomes ``"type": "string"`` with
  ``"nullable": true``; ``null`` options of ``anyOf`` and ``enum`` likewise;
* ``const`` becomes a one-value ``enum``, ``oneOf`` becomes ``anyOf``;
* an ``enum`` without a type gets ``"type": "string"``; non-string enums,
  which Gemini does not support, are dropped and only their type is kept;
* unsupported keywords (``additionalProperties``, ``$defs``, ``$schema``,
  ``uniqueItems``, ``exclusiveMinimum``, ``propertyNames``, ``not``,
  ``allOf``, ``default`` and others) are removed, and ``required`` keeps only
  declared properties;
* a function without parameters gets none, instead of an empty object.

Dropped keywords are constraints, not data: NLG still checks every call
against the original schema before running a tool, and reports violations to
the model.
"""

from __future__ import annotations

KEPT = {
    "type",
    "description",
    "title",
    "nullable",
    "enum",
    "properties",
    "required",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "anyOf",
}
STRING_FORMATS = {"enum", "date-time"}
MAX_REF_DEPTH = 2  # deeper recursive definitions become plain objects


def _type_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _resolve(node: dict, defs: dict, depth: int) -> tuple[dict, int]:
    seen = 0
    while isinstance(node, dict) and "$ref" in node:
        if depth + seen >= MAX_REF_DEPTH:
            return {"type": "object", "description": node.get("description", "")}, depth + seen
        name = node["$ref"].rsplit("/", 1)[-1]
        target = defs.get(name)
        if target is None:
            return {"type": "object"}, depth + seen
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        seen += 1
    return node, depth + seen


def _convert(node, defs: dict, depth: int):
    if not isinstance(node, dict):
        return {}
    node, depth = _resolve(node, defs, depth)
    node = dict(node)
    if "const" in node:
        node["enum"] = [node.pop("const")]
    if "oneOf" in node:
        node["anyOf"] = node.pop("oneOf") + node.get("anyOf", [])
    result: dict = {}

    kind = node.get("type")
    if isinstance(kind, list):
        types = [t for t in kind if t != "null"]
        if len(types) < len(kind):
            result["nullable"] = True
        if len(types) == 1:
            kind = types[0]
        else:
            node["anyOf"] = [{"type": t} for t in types] + node.get("anyOf", [])
            kind = None

    if "enum" in node:
        values = [value for value in node["enum"] if value is not None]
        if len(values) < len(node["enum"]):
            result["nullable"] = True
        if values and all(isinstance(value, str) for value in values):
            result["enum"] = values
            kind = "string"
        elif values:
            kind = kind or _type_of(values[0])

    if "anyOf" in node:
        options = []
        for option in node["anyOf"]:
            if isinstance(option, dict) and option.get("type") == "null":
                result["nullable"] = True
                continue
            converted = _convert(option, defs, depth)
            if converted:
                options.append(converted)
        if len(options) == 1 and kind is None:
            merged = options[0]
            if result.get("nullable"):
                merged["nullable"] = True
            for key in ("description", "title"):
                if key in node:
                    merged[key] = node[key]
            return merged
        if options:
            result["anyOf"] = options

    if kind is None and "properties" in node:
        kind = "object"
    if kind is None and "items" in node:
        kind = "array"
    if kind is not None:
        result["type"] = kind

    for key in (
        "description",
        "title",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
    ):
        if key in node:
            result[key] = node[key]
    if node.get("format") in STRING_FORMATS and kind == "string":
        result["format"] = node["format"]
    if kind == "object" and isinstance(node.get("properties"), dict) and node["properties"]:
        result["properties"] = {
            name: _convert(value, defs, depth) for name, value in node["properties"].items()
        }
        required = [name for name in node.get("required", []) if name in result["properties"]]
        if required:
            result["required"] = required
    if kind == "array":
        result["items"] = _convert(node.get("items", {}), defs, depth) or {"type": "string"}
    if not result:
        result["type"] = "string"
    return {key: value for key, value in result.items() if key in KEPT or key == "format"}


def gemini_parameters(schema: dict) -> dict | None:
    """Parameters for a Gemini function declaration, or None if it takes none."""
    converted = _convert(schema or {}, (schema or {}).get("$defs", {}), 0)
    if converted.get("type") == "object" and not converted.get("properties"):
        return None
    return converted
