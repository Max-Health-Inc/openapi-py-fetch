"""Schema resolution and model emission."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openapi_py_fetch.generator import extract_operations, generate_api_class, generate_client_package, generate_method
from openapi_py_fetch.models import generate_models_module
from openapi_py_fetch.schema_registry import SchemaRegistry, literal_of, norm_type

FIXTURES = Path(__file__).parent.parent / "examples"


def spec_with(schemas: dict, paths: dict | None = None) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1.0.0"},
        "paths": paths or {},
        "components": {"schemas": schemas},
    }


class TestNormType:
    def test_plain(self):
        assert norm_type({"type": "string"}) == ("string", False)

    def test_nullable_flag(self):
        assert norm_type({"type": "string", "nullable": True}) == ("string", True)

    def test_type_array(self):
        assert norm_type({"type": ["string", "null"]}) == ("string", True)

    def test_only_null(self):
        assert norm_type({"type": ["null"]}) == ("null", True)

    def test_missing(self):
        assert norm_type({}) == (None, False)


class TestLiteralOf:
    def test_strings(self):
        assert literal_of(["a", "b"]) == 'Literal["a", "b"]'

    def test_ints(self):
        assert literal_of([1, 2]) == "Literal[1, 2]"

    def test_unrepresentable(self):
        assert literal_of([{"a": 1}]) == "Any"

    def test_empty(self):
        assert literal_of([]) == "Any"


class TestResolution:
    def test_follows_ref(self):
        registry = SchemaRegistry(spec_with({"Kind": {"type": "string"}}))
        assert registry.python_type({"$ref": "#/components/schemas/Kind"}) == "str"

    def test_ref_to_enum_becomes_literal(self):
        registry = SchemaRegistry(spec_with({"Kind": {"type": "string", "enum": ["a", "b"]}}))
        assert registry.python_type({"$ref": "#/components/schemas/Kind"}) == 'Literal["a", "b"]'

    def test_ref_to_object_becomes_model(self):
        registry = SchemaRegistry(spec_with({"Pet": {"type": "object", "properties": {"id": {"type": "string"}}}}))
        assert registry.python_type({"$ref": "#/components/schemas/Pet"}) == "Pet"
        assert "Pet" in registry.models

    def test_unresolvable_ref(self):
        registry = SchemaRegistry(spec_with({}))
        assert registry.python_type({"$ref": "#/components/schemas/Missing"}) == "Any"

    def test_remote_ref_not_followed(self):
        registry = SchemaRegistry(spec_with({}))
        assert registry.python_type({"$ref": "https://example.test/schema.json#/Pet"}) == "Any"

    def test_circular_ref_terminates(self):
        registry = SchemaRegistry(spec_with({"A": {"$ref": "#/components/schemas/A"}}))
        assert registry.python_type({"$ref": "#/components/schemas/A"}) == "Any"

    def test_self_referencing_model(self):
        registry = SchemaRegistry(
            spec_with(
                {
                    "Node": {
                        "type": "object",
                        "properties": {"kids": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}}},
                    }
                }
            )
        )
        assert registry.python_type({"$ref": "#/components/schemas/Node"}) == "Node"
        source, names = generate_models_module(registry, "T")
        assert names == ["Node"]
        assert "kids: NotRequired[list[Node]]" in source


class TestTypeMapping:
    def setup_method(self):
        self.registry = SchemaRegistry(spec_with({}))

    def test_array_of_scalars(self):
        assert self.registry.python_type({"type": "array", "items": {"type": "integer"}}) == "list[int]"

    def test_free_form_object(self):
        assert self.registry.python_type({"type": "object"}) == "dict[str, Any]"

    def test_additional_properties(self):
        schema = {"type": "object", "additionalProperties": {"type": "integer"}}
        assert self.registry.python_type(schema) == "dict[str, int]"

    def test_binary_format(self):
        assert self.registry.python_type({"type": "string", "format": "binary"}) == "bytes"

    def test_nullable_type_array(self):
        assert self.registry.python_type({"type": ["integer", "null"]}) == "int | None"

    def test_union(self):
        schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        assert self.registry.python_type(schema) == "str | int"

    def test_union_collapses_duplicates(self):
        schema = {"anyOf": [{"type": "string"}, {"type": "string"}]}
        assert self.registry.python_type(schema) == "str"

    def test_empty_schema(self):
        assert self.registry.python_type({}) == "Any"

    def test_none(self):
        assert self.registry.python_type(None) == "Any"


class TestAllOf:
    def test_merges_properties_and_required(self):
        registry = SchemaRegistry(
            spec_with(
                {
                    "Base": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
                    "Thing": {
                        "allOf": [
                            {"$ref": "#/components/schemas/Base"},
                            {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}}},
                        ]
                    },
                }
            )
        )
        assert registry.python_type({"$ref": "#/components/schemas/Thing"}) == "Thing"
        source, _ = generate_models_module(registry, "T")
        assert "    id: str" in source
        assert "    kind: str" in source


class TestModelNaming:
    def test_inline_object_uses_title(self):
        registry = SchemaRegistry(spec_with({}))
        schema = {"type": "object", "title": "Widget", "properties": {"a": {"type": "string"}}}
        assert registry.python_type(schema, "FallbackHint") == "Widget"

    def test_inline_object_falls_back_to_hint(self):
        registry = SchemaRegistry(spec_with({}))
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        assert registry.python_type(schema, "CreateThingRequest") == "CreateThingRequest"

    def test_anonymous_object(self):
        registry = SchemaRegistry(spec_with({}))
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        assert registry.python_type(schema) == "Model1"

    def test_name_collision_is_suffixed(self):
        registry = SchemaRegistry(spec_with({}))
        first = {"type": "object", "title": "Widget", "properties": {"a": {"type": "string"}}}
        second = {"type": "object", "title": "Widget", "properties": {"b": {"type": "integer"}}}
        assert registry.python_type(first) == "Widget"
        assert registry.python_type(second) == "Widget2"

    def test_non_pascal_ref_name_is_sanitized(self):
        registry = SchemaRegistry(
            spec_with({"api__files__Body": {"type": "object", "properties": {"a": {"type": "string"}}}})
        )
        assert registry.python_type({"$ref": "#/components/schemas/api__files__Body"}) == "ApiFilesBody"


class TestModelEmission:
    def test_required_and_optional_fields(self):
        registry = SchemaRegistry(
            spec_with(
                {
                    "Pet": {
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
                    }
                }
            )
        )
        registry.python_type({"$ref": "#/components/schemas/Pet"})
        source, names = generate_models_module(registry, "T")
        assert names == ["Pet"]
        assert "    id: str" in source
        assert "    name: NotRequired[str]" in source
        assert "from typing import NotRequired, TypedDict" in source

    def test_non_identifier_keys_use_functional_form(self):
        registry = SchemaRegistry(
            spec_with(
                {
                    "Weird": {
                        "type": "object",
                        "properties": {"not-ok": {"type": "string"}, "class": {"type": "boolean"}},
                    }
                }
            )
        )
        registry.python_type({"$ref": "#/components/schemas/Weird"})
        source, _ = generate_models_module(registry, "T")
        assert "Weird = TypedDict(" in source
        assert '"not-ok": NotRequired[str],' in source

    def test_no_models(self):
        source, names = generate_models_module(SchemaRegistry(spec_with({})), "T")
        assert names == []
        assert "No object schemas" in source

    def test_deterministic(self):
        def build() -> str:
            registry = SchemaRegistry(
                spec_with(
                    {
                        "B": {"type": "object", "properties": {"x": {"type": "string"}}},
                        "A": {"type": "object", "properties": {"y": {"type": "integer"}}},
                    }
                )
            )
            registry.python_type({"$ref": "#/components/schemas/B"})
            registry.python_type({"$ref": "#/components/schemas/A"})
            return generate_models_module(registry, "T")[0]

        assert build() == build()


class TestTypedMethods:
    def _spec(self) -> dict:
        return spec_with(
            {
                "Kind": {"type": "string", "enum": ["a", "b"]},
                "Pet": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
            },
            {
                "/pets/{petId}": {
                    "get": {
                        "operationId": "getPet",
                        "tags": ["pets"],
                        "parameters": [
                            {"name": "petId", "in": "path", "required": True, "schema": {"type": "string"}},
                            {"name": "kind", "in": "query", "schema": {"$ref": "#/components/schemas/Kind"}},
                        ],
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}},
                            }
                        },
                    }
                }
            },
        )

    def test_return_type_from_response_schema(self):
        spec = self._spec()
        registry = SchemaRegistry(spec)
        op = extract_operations(spec)["pets"][0]
        code = generate_method(op, registry)
        assert "-> Pet:" in code
        assert "-> tuple[Pet, int, dict[str, str]]:" in code

    def test_enum_param_is_literal(self):
        spec = self._spec()
        op = extract_operations(spec)["pets"][0]
        code = generate_method(op, SchemaRegistry(spec))
        assert 'kind: Literal["a", "b"] | None = None' in code

    def test_without_registry_returns_object(self):
        spec = self._spec()
        op = extract_operations(spec)["pets"][0]
        code = generate_method(op)
        assert "-> object:" in code
        assert "kind: str | None = None" in code

    def test_api_class_imports_models(self):
        spec = self._spec()
        registry = SchemaRegistry(spec)
        _, _, content = generate_api_class("pets", extract_operations(spec)["pets"], "T", "", registry)
        assert "from openapi_client.models import Pet" in content

    def test_optional_nullable_param_is_not_double_optional(self):
        spec = spec_with(
            {},
            {
                "/x": {
                    "get": {
                        "operationId": "x",
                        "tags": ["x"],
                        "parameters": [{"name": "n", "in": "query", "schema": {"type": ["integer", "null"]}}],
                        "responses": {},
                    }
                }
            },
        )
        code = generate_method(extract_operations(spec)["x"][0], SchemaRegistry(spec))
        assert "n: int | None = None" in code
        assert "| None | None" not in code


class TestGeneratedPackageTypeChecks:
    def test_petstore_models_are_generated_and_importable(self, tmp_path):
        with open(FIXTURES / "petstore.json") as f:
            spec = json.load(f)
        assert generate_client_package(spec, tmp_path) is True

        models = (tmp_path / "openapi_client" / "models" / "__init__.py").read_text(encoding="utf-8")
        assert "TypedDict" in models

        sys.path.insert(0, str(tmp_path))
        try:
            for module in [m for m in list(sys.modules) if m.startswith("openapi_client")]:
                del sys.modules[module]
            import openapi_client.models as generated_models

            assert generated_models.__all__
        finally:
            sys.path.remove(str(tmp_path))

    def test_generated_package_type_checks(self, tmp_path):
        with open(FIXTURES / "advanced.json") as f:
            spec = json.load(f)
        generate_client_package(spec, tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--ignore-missing-imports", "openapi_client"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        if "No module named mypy" in result.stderr:
            pytest.skip("mypy not installed")
        assert result.returncode == 0, result.stdout + result.stderr
