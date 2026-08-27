"""Schema resolution: follows $ref, normalizes 3.1 type arrays, names inline models.

The registry is the single place that turns an OpenAPI schema into a Python type
annotation. Object schemas with declared properties become named models, which
:mod:`openapi_py_fetch.models` later emits as TypedDicts; everything else maps
straight to a builtin, a Literal, a union, or Any.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .naming import pascal_case

SCALARS = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
}

FORMATS = {
    "binary": "bytes",
}


def norm_type(schema: dict[str, Any]) -> tuple[str | None, bool]:
    """Normalize an OpenAPI 3.1 type array like ["string", "null"] to (type, nullable)."""
    raw = schema.get("type")
    if isinstance(raw, list):
        non_null = [t for t in raw if t != "null"]
        return (non_null[0] if non_null else "null"), len(non_null) < len(raw)
    return raw, bool(schema.get("nullable", False))


def literal_of(values: list[Any]) -> str:
    """Render an enum as a Literal, or Any when the values are not literal-able."""
    parts: list[str] = []
    for value in values:
        if isinstance(value, str):
            parts.append(json.dumps(value))
        elif isinstance(value, (bool, int)):
            parts.append(repr(value))
        else:
            return "Any"
    return f"Literal[{', '.join(parts)}]" if parts else "Any"


class SchemaRegistry:
    """Resolves schemas to Python types and collects the models worth naming."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.models: dict[str, dict[str, Any]] = {}
        self._names_by_ref: dict[str, str] = {}
        self._anonymous = 0

    # -- resolution ----------------------------------------------------------

    def lookup(self, ref: str) -> dict[str, Any]:
        """Resolve a local JSON pointer against the spec. Remote refs are not followed."""
        if not ref.startswith("#/"):
            return {}
        node: Any = self.spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return {}
            node = node[part]
        return node if isinstance(node, dict) else {}

    def resolve(self, schema: dict[str, Any] | None) -> dict[str, Any]:
        """Follow a $ref chain to the schema it points at."""
        seen: set[str] = set()
        while isinstance(schema, dict) and "$ref" in schema:
            ref = schema["$ref"]
            if ref in seen:
                return {}
            seen.add(ref)
            schema = self.lookup(ref)
        return schema if isinstance(schema, dict) else {}

    # -- type mapping --------------------------------------------------------

    def python_type(self, schema: dict[str, Any] | None, hint: str = "") -> str:
        """Map a schema to a Python type annotation, naming models on the way."""
        if not isinstance(schema, dict) or not schema:
            return "Any"

        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in self._names_by_ref:
                return self._names_by_ref[ref]
            return self._type_of(self.resolve(schema), _name_from_ref(ref) or hint, ref)

        return self._type_of(schema, hint)

    def _type_of(self, schema: dict[str, Any], hint: str, ref: str | None = None) -> str:
        if not schema:
            return "Any"

        schema_type, nullable = norm_type(schema)

        if schema.get("enum"):
            base = literal_of(schema["enum"])
        elif schema.get("allOf"):
            base = self._object_type(self._merge_all_of(schema), hint, ref)
        elif schema.get("oneOf") or schema.get("anyOf"):
            variants = schema.get("oneOf") or schema.get("anyOf")
            base = self._union_type(variants, hint)
        elif schema_type == "array":
            base = f"list[{self.python_type(schema.get('items', {}), hint + 'Item')}]"
        elif schema_type == "object" or "properties" in schema:
            base = self._object_type(schema, hint, ref)
        elif schema_type in SCALARS:
            base = FORMATS.get(schema.get("format", ""), SCALARS[schema_type])
        else:
            base = "Any"

        if nullable and base not in ("Any", "None"):
            return f"{base} | None"
        return base

    def _union_type(self, variants: list[Any], hint: str) -> str:
        parts: list[str] = []
        for index, variant in enumerate(variants):
            rendered = self.python_type(variant, f"{hint}Variant{index + 1}" if hint else "")
            if rendered not in parts:
                parts.append(rendered)
        if not parts or "Any" in parts:
            return "Any"
        return " | ".join(parts)

    def _object_type(self, schema: dict[str, Any], hint: str, ref: str | None = None) -> str:
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict) and extra:
                return f"dict[str, {self.python_type(extra, hint + 'Value')}]"
            return "dict[str, Any]"
        return self.register(schema, hint, ref)

    def _merge_all_of(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Flatten allOf into one object schema; later members win on conflict."""
        merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for key in ("title", "description"):
            if key in schema:
                merged[key] = schema[key]
        members = [*schema.get("allOf", []), {k: v for k, v in schema.items() if k != "allOf"}]
        for member in members:
            resolved = self.resolve(member)
            properties = resolved.get("properties")
            if isinstance(properties, dict):
                merged["properties"].update(properties)
            for name in resolved.get("required", []):
                if name not in merged["required"]:
                    merged["required"].append(name)
        return merged

    # -- model naming --------------------------------------------------------

    def register(self, schema: dict[str, Any], hint: str = "", ref: str | None = None) -> str:
        """Name an object schema and remember it as a model. Returns the model name."""
        name = self._pick_name(schema, hint, ref)
        if ref:
            self._names_by_ref[ref] = name
        self.models[name] = schema
        return name

    def _pick_name(self, schema: dict[str, Any], hint: str, ref: str | None) -> str:
        for candidate in (_name_from_ref(ref or ""), schema.get("title", ""), hint):
            name = _sanitize(candidate)
            if not name:
                continue
            existing = self.models.get(name)
            if existing is None or existing is schema:
                return name
            suffix = 2
            while f"{name}{suffix}" in self.models and self.models[f"{name}{suffix}"] is not schema:
                suffix += 1
            return f"{name}{suffix}"

        self._anonymous += 1
        return f"Model{self._anonymous}"


def _name_from_ref(ref: str) -> str:
    """Take the last pointer segment of a $ref as the model name."""
    if not ref:
        return ""
    return ref.rstrip("/").rsplit("/", 1)[-1]


def _sanitize(name: str) -> str:
    """Coerce a schema name into a valid PascalCase Python identifier."""
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", name or "").strip("_")
    if not cleaned:
        return ""
    result = cleaned if re.fullmatch(r"[A-Z][0-9a-zA-Z]*", cleaned) else pascal_case(cleaned)
    if not result or result[0].isdigit():
        result = "Model" + result
    return result
