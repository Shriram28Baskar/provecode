"""A small JSON Schema validator for the report schema (engine/schema/report.v2.json), stdlib only.

It implements exactly the keywords that schema uses: type, const, enum, required, properties,
additionalProperties, items, minItems, maxItems, minimum, minLength, pattern, anyOf and local
$ref. Any other keyword in the schema is an error, so the schema cannot silently use a keyword
this validator ignores.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "schema" / "report.v2.json").read_text(encoding="utf-8"))
KNOWN = {"$schema", "$id", "title", "description", "$defs", "type", "const", "enum", "required", "properties",
         "additionalProperties", "items", "minItems", "maxItems", "minimum", "minLength", "pattern", "anyOf", "$ref"}
TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool,
         "null": type(None)}


def _is(value, t: str) -> bool:
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[t])


def errors(value, schema: dict = SCHEMA, path: str = "$") -> list[str]:
    unknown = set(schema) - KNOWN
    if unknown:
        return [f"{path}: schema uses unsupported keywords {sorted(unknown)}"]
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        return errors(value, SCHEMA["$defs"][name], path)
    out: list[str] = []
    if "anyOf" in schema:
        if all(errors(value, s, path) for s in schema["anyOf"]):
            out.append(f"{path}: matches none of anyOf")
        return out
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is(value, t) for t in ts):
            return [f"{path}: expected {ts}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema \
            and value < schema["minimum"]:
        out.append(f"{path}: {value} < {schema['minimum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                out += errors(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing {k!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                out += errors(v, props[k], f"{path}.{k}")
            elif extra is False:
                out.append(f"{path}: unexpected property {k!r}")
            elif isinstance(extra, dict):
                out += errors(v, extra, f"{path}.{k}")
    return out
