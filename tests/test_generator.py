"""Tests for openapi_py_fetch.generator — naming helpers, operation extraction, code generation."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

from openapi_py_fetch.generator import (
    enrich_spec_tags,
    extract_operations,
    generate_api_class,
    generate_client_package,
    generate_method,
    map_schema_to_python_type,
    pascal_case,
    sanitize_method_name,
    sanitize_pep440_version,
    snake_case,
)

FIXTURES = Path(__file__).parent.parent / "examples"


# =========================================================================
# Naming helpers
# =========================================================================


class TestSnakeCase:
    def test_camel(self):
        assert snake_case("getPetById") == "get_pet_by_id"

    def test_pascal(self):
        assert snake_case("PetApi") == "pet_api"

    def test_already_snake(self):
        assert snake_case("get_pet") == "get_pet"

    def test_hyphenated(self):
        assert snake_case("find-by-status") == "find_by_status"

    def test_consecutive_caps(self):
        assert snake_case("getHTTPResponse") == "get_http_response"


class TestPascalCase:
    def test_snake(self):
        assert pascal_case("pet_api") == "PetApi"

    def test_hyphenated(self):
        assert pascal_case("user-service") == "UserService"

    def test_single(self):
        assert pascal_case("pet") == "Pet"

    def test_spaces(self):
        assert pascal_case("my cool api") == "MyCoolApi"


class TestSanitizeMethodName:
    def test_basic(self):
        assert sanitize_method_name("getPetById") == "get_pet_by_id"

    def test_special_chars(self):
        result = sanitize_method_name("get-item-123-special!")
        assert result.isidentifier()
        assert "!" not in result

    def test_leading_digit(self):
        result = sanitize_method_name("123test")
        assert result.startswith("op_")

    def test_empty(self):
        result = sanitize_method_name("")
        assert result == ""


class TestSanitizePep440:
    def test_basic(self):
        assert sanitize_pep440_version("1.0.0") == "1.0.0"

    def test_beta(self):
        assert sanitize_pep440_version("2.0.0-beta1") == "2.0.0b1"

    def test_alpha(self):
        assert sanitize_pep440_version("1.0.0-alpha2") == "1.0.0a2"

    def test_rc(self):
        assert sanitize_pep440_version("3.0.0-rc1") == "3.0.0rc1"

    def test_invalid(self):
        assert sanitize_pep440_version("latest") == "0.0.0"


# =========================================================================
# Type mapping
# =========================================================================


class TestTypeMapping:
    def test_string(self):
        assert map_schema_to_python_type({"type": "string"}) == "str"

    def test_integer(self):
        assert map_schema_to_python_type({"type": "integer"}) == "int"

    def test_number(self):
        assert map_schema_to_python_type({"type": "number"}) == "float"

    def test_boolean(self):
        assert map_schema_to_python_type({"type": "boolean"}) == "bool"

    def test_array(self):
        assert map_schema_to_python_type({"type": "array", "items": {"type": "string"}}) == "list[str]"

    def test_object(self):
        assert map_schema_to_python_type({"type": "object"}) == "dict[str, Any]"

    def test_nullable(self):
        assert map_schema_to_python_type({"type": "string", "nullable": True}) == "str | None"

    def test_none(self):
        assert map_schema_to_python_type(None) == "str"

    def test_unknown_type(self):
        assert map_schema_to_python_type({"type": "foobar"}) == "str"

    def test_nested_array(self):
        schema = {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}
        assert map_schema_to_python_type(schema) == "list[list[int]]"


# =========================================================================
# Operation extraction
# =========================================================================


class TestExtractOperations:
    @pytest.fixture()
    def petstore_spec(self):
        with open(FIXTURES / "petstore.json") as f:
            return json.load(f)

    def test_tag_grouping(self, petstore_spec):
        ops = extract_operations(petstore_spec)
        assert "pet" in ops
        assert "store" in ops

    def test_pet_operations(self, petstore_spec):
        ops = extract_operations(petstore_spec)
        pet_op_ids = [op["operation_id"] for op in ops["pet"]]
        assert "addPet" in pet_op_ids
        assert "getPetById" in pet_op_ids
        assert "deletePet" in pet_op_ids
        assert "findPetsByStatus" in pet_op_ids

    def test_store_operations(self, petstore_spec):
        ops = extract_operations(petstore_spec)
        store_op_ids = [op["operation_id"] for op in ops["store"]]
        assert "getInventory" in store_op_ids
        assert "getOrderById" in store_op_ids

    def test_parameters_extracted(self, petstore_spec):
        ops = extract_operations(petstore_spec)
        get_pet = next(op for op in ops["pet"] if op["operation_id"] == "getPetById")
        assert len(get_pet["parameters"]) == 1
        assert get_pet["parameters"][0]["name"] == "petId"
        assert get_pet["parameters"][0]["in"] == "path"
        assert get_pet["parameters"][0]["required"] is True

    def test_body_extracted(self, petstore_spec):
        ops = extract_operations(petstore_spec)
        add_pet = next(op for op in ops["pet"] if op["operation_id"] == "addPet")
        assert add_pet["body_schema"] is not None
        assert add_pet["body_required"] is True

    def test_empty_spec(self):
        ops = extract_operations({"paths": {}})
        assert ops == {}

    def test_no_paths_key(self):
        ops = extract_operations({})
        assert ops == {}


# =========================================================================
# Tag enrichment
# =========================================================================


class TestEnrichSpecTags:
    def test_discovers_undeclared(self):
        spec = {
            "tags": [],
            "paths": {
                "/x": {"get": {"tags": ["alpha"], "operationId": "op1", "responses": {}}},
                "/y": {"post": {"tags": ["beta"], "operationId": "op2", "responses": {}}},
            },
        }
        discovered = enrich_spec_tags(spec)
        assert "alpha" in discovered
        assert "beta" in discovered
        assert len(spec["tags"]) == 2

    def test_skips_declared(self):
        spec = {
            "tags": [{"name": "pet"}],
            "paths": {
                "/pet": {"get": {"tags": ["pet"], "operationId": "op1", "responses": {}}},
            },
        }
        discovered = enrich_spec_tags(spec)
        assert discovered == []
        assert len(spec["tags"]) == 1

    def test_no_duplicates(self):
        spec = {
            "tags": [],
            "paths": {
                "/a": {"get": {"tags": ["t1"], "operationId": "op1", "responses": {}}},
                "/b": {"post": {"tags": ["t1"], "operationId": "op2", "responses": {}}},
            },
        }
        discovered = enrich_spec_tags(spec)
        assert discovered.count("t1") == 1


# =========================================================================
# Method generation
# =========================================================================


class TestGenerateMethod:
    def _make_op(self, **overrides):
        base = {
            "operation_id": "getPetById",
            "method": "GET",
            "path": "/pet/{petId}",
            "summary": "Find pet by ID",
            "description": "",
            "parameters": [
                {
                    "name": "petId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "integer"},
                    "description": "ID of pet",
                },
            ],
            "body_schema": None,
            "body_required": False,
            "response": {},
        }
        base.update(overrides)
        return base

    def test_method_name(self):
        code = generate_method(self._make_op())
        assert "def get_pet_by_id(" in code

    def test_has_with_http_info(self):
        code = generate_method(self._make_op())
        assert "def get_pet_by_id_with_http_info(" in code

    def test_path_param_typed(self):
        code = generate_method(self._make_op())
        assert "pet_id: int" in code

    def test_body_param(self):
        code = generate_method(
            self._make_op(
                operation_id="addPet",
                method="POST",
                path="/pet",
                body_schema={"type": "object"},
                body_required=True,
                parameters=[],
            )
        )
        assert "body: dict[str, Any]" in code

    def test_optional_query_param(self):
        code = generate_method(
            self._make_op(
                operation_id="findPetsByStatus",
                method="GET",
                path="/pet/findByStatus",
                parameters=[
                    {
                        "name": "status",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": "Status",
                    },
                ],
            )
        )
        assert "status: str | None = None" in code

    def test_docstring_present(self):
        code = generate_method(self._make_op())
        assert '"""Find pet by ID' in code

    def test_call_api_invoked(self):
        code = generate_method(self._make_op())
        assert "self.api_client.call_api(" in code
        assert '"/pet/{petId}"' in code


# =========================================================================
# API class generation
# =========================================================================


class TestGenerateApiClass:
    def test_class_name(self):
        ops = [
            {
                "operation_id": "getInventory",
                "method": "GET",
                "path": "/store/inventory",
                "summary": "Get inventory",
                "description": "",
                "parameters": [],
                "body_schema": None,
                "body_required": False,
                "response": {},
            },
        ]
        class_name, module_name, content = generate_api_class("store", ops, "Petstore", "A Petstore")
        assert class_name == "StoreApi"
        assert module_name == "store_api"
        assert "class StoreApi:" in content
        assert "from openapi_py_fetch import ApiClient" in content

    def test_multiple_methods(self):
        ops = [
            {
                "operation_id": f"op{i}",
                "method": "GET",
                "path": f"/path{i}",
                "summary": f"Op {i}",
                "description": "",
                "parameters": [],
                "body_schema": None,
                "body_required": False,
                "response": {},
            }
            for i in range(3)
        ]
        _, _, content = generate_api_class("multi", ops, "Multi", "")
        assert content.count("def op") == 6  # 3 normal + 3 _with_http_info


# =========================================================================
# Full package generation
# =========================================================================


class TestGenerateClientPackage:
    @pytest.fixture()
    def petstore_spec(self):
        with open(FIXTURES / "petstore.json") as f:
            return json.load(f)

    @pytest.fixture()
    def advanced_spec(self):
        with open(FIXTURES / "advanced.json") as f:
            return json.load(f)

    def test_petstore_generation(self, petstore_spec, tmp_path):
        ok = generate_client_package(petstore_spec, tmp_path)
        assert ok is True

        client_dir = tmp_path / "openapi_client"
        assert client_dir.exists()
        assert (client_dir / "__init__.py").exists()
        assert (client_dir / "api" / "pet_api.py").exists()
        assert (client_dir / "api" / "store_api.py").exists()
        assert (tmp_path / "pyproject.toml").exists()

    def test_petstore_importable(self, petstore_spec, tmp_path):
        generate_client_package(petstore_spec, tmp_path)

        # Add to sys.path and verify import
        sys.path.insert(0, str(tmp_path))
        try:
            # Clear any stale cached modules
            for mod in list(sys.modules):
                if mod.startswith("openapi_client"):
                    del sys.modules[mod]

            import openapi_client

            assert hasattr(openapi_client, "PetApi")
            assert hasattr(openapi_client, "StoreApi")
        finally:
            sys.path.remove(str(tmp_path))

    def test_introspection(self, petstore_spec, tmp_path):
        generate_client_package(petstore_spec, tmp_path)

        sys.path.insert(0, str(tmp_path))
        try:
            for mod in list(sys.modules):
                if mod.startswith("openapi_client"):
                    del sys.modules[mod]

            from openapi_client.api.pet_api import PetApi

            pet = PetApi()

            # Method exists and is callable
            assert callable(getattr(pet, "get_pet_by_id"))

            # Signature has typed parameters
            sig = inspect.signature(pet.get_pet_by_id)
            assert "pet_id" in sig.parameters

            # Docstring exists
            doc = inspect.getdoc(pet.get_pet_by_id)
            assert doc is not None
            assert "Find pet by ID" in doc
        finally:
            sys.path.remove(str(tmp_path))

    def test_advanced_spec_version_coerced(self, advanced_spec, tmp_path):
        generate_client_package(advanced_spec, tmp_path)

        pyproject = (tmp_path / "pyproject.toml").read_text()
        # 2.0.0-beta1 should become 2.0.0b1
        assert "2.0.0b1" in pyproject

    def test_undeclared_tags_auto_discovered(self, advanced_spec, tmp_path):
        """The advanced spec has tags used in paths but not declared in spec.tags."""
        generate_client_package(advanced_spec, tmp_path)

        api_dir = tmp_path / "openapi_client" / "api"
        # "employees" and "departments" are declared or used
        py_files = [f.name for f in api_dir.glob("*.py") if f.name != "__init__.py"]
        assert "employees_api.py" in py_files
        assert "departments_api.py" in py_files

    def test_header_param_in_generated_code(self, advanced_spec, tmp_path):
        generate_client_package(advanced_spec, tmp_path)

        content = (tmp_path / "openapi_client" / "api" / "employees_api.py").read_text()
        # getEmployee has X-Request-Id header param
        assert "x_request_id" in content
        assert "_header_params" in content

    def test_custom_enrich_fn(self, petstore_spec, tmp_path):
        """Verify enrich_tags_fn parameter is called."""
        called = []

        def my_enrich(spec):
            called.append(True)
            return enrich_spec_tags(spec)

        generate_client_package(petstore_spec, tmp_path, enrich_tags_fn=my_enrich)
        assert len(called) == 1

    def test_empty_spec(self, tmp_path):
        """Spec with no paths generates but verification warns."""
        spec = {"openapi": "3.0.3", "info": {"title": "Empty", "version": "0.0.1"}, "paths": {}}
        ok = generate_client_package(spec, tmp_path)
        # No API classes → verification returns False
        assert ok is False

    def test_regeneration_cleans_old(self, petstore_spec, tmp_path):
        """Running twice should clean old output."""
        generate_client_package(petstore_spec, tmp_path)
        # Create a stale file
        stale = tmp_path / "openapi_client" / "api" / "stale_api.py"
        stale.write_text("# stale", encoding="utf-8")

        generate_client_package(petstore_spec, tmp_path)
        assert not stale.exists()

    def test_pyproject_depends_on_py_fetch(self, petstore_spec, tmp_path):
        generate_client_package(petstore_spec, tmp_path)
        pyproject = (tmp_path / "pyproject.toml").read_text()
        assert "openapi-py-fetch>=0.1" in pyproject
        assert "httpx" not in pyproject

    def test_tags_filter_generates_subset(self, petstore_spec, tmp_path):
        """Only requested tags are generated."""
        ok = generate_client_package(petstore_spec, tmp_path, tags=["store"])
        assert ok is True
        api_dir = tmp_path / "openapi_client" / "api"
        assert (api_dir / "store_api.py").exists()
        assert not (api_dir / "pet_api.py").exists()


# =========================================================================
# CLI (__main__)
# =========================================================================


class TestCLI:
    def test_missing_spec(self, tmp_path, monkeypatch):
        """CLI exits 1 for missing spec file."""
        monkeypatch.setattr(
            "sys.argv",
            ["openapi-py-fetch", str(tmp_path / "nonexistent.json")],
        )
        from openapi_py_fetch.__main__ import main

        assert main() == 1

    def test_valid_spec(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "Test", "version": "1.0.0"},
                    "tags": [{"name": "default"}],
                    "paths": {
                        "/test": {
                            "get": {
                                "tags": ["default"],
                                "operationId": "getTest",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                }
            )
        )
        out = tmp_path / "out"
        monkeypatch.setattr(
            "sys.argv",
            ["openapi-py-fetch", str(spec_path), str(out)],
        )
        from openapi_py_fetch.__main__ import main

        result = main()
        assert result == 0
        assert (out / "openapi_client" / "__init__.py").exists()

    def test_dry_run(self, tmp_path, monkeypatch, capsys):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "DryTest", "version": "1.0.0"},
                    "paths": {
                        "/items": {
                            "get": {
                                "tags": ["items"],
                                "operationId": "listItems",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                }
            )
        )
        monkeypatch.setattr(
            "sys.argv",
            ["openapi-py-fetch", str(spec_path), "--dry-run"],
        )
        from openapi_py_fetch.__main__ import main

        result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "Dry run complete" in captured.out
        # No output dir created
        assert not (tmp_path / "generated_openapi" / "openapi_client").exists()

    def test_tags_filter(self, tmp_path, monkeypatch):
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "TagTest", "version": "1.0.0"},
                    "paths": {
                        "/a": {
                            "get": {
                                "tags": ["alpha"],
                                "operationId": "opA",
                                "responses": {"200": {"description": "OK"}},
                            }
                        },
                        "/b": {
                            "get": {"tags": ["beta"], "operationId": "opB", "responses": {"200": {"description": "OK"}}}
                        },
                    },
                }
            )
        )
        out = tmp_path / "out"
        monkeypatch.setattr(
            "sys.argv",
            ["openapi-py-fetch", str(spec_path), str(out), "--tags", "alpha"],
        )
        from openapi_py_fetch.__main__ import main

        result = main()
        assert result == 0
        api_dir = out / "openapi_client" / "api"
        assert (api_dir / "alpha_api.py").exists()
        assert not (api_dir / "beta_api.py").exists()

    def test_invalid_spec_no_openapi_key(self, tmp_path, monkeypatch, capsys):
        spec_path = tmp_path / "bad.json"
        spec_path.write_text(json.dumps({"info": {"title": "Bad"}}))
        monkeypatch.setattr(
            "sys.argv",
            ["openapi-py-fetch", str(spec_path)],
        )
        from openapi_py_fetch.__main__ import main

        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "Missing" in captured.out

    def test_url_loading_failure(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            ["openapi-py-fetch", "https://invalid.example.test/spec.json"],
        )
        from openapi_py_fetch.__main__ import main

        result = main()
        assert result == 1


# =========================================================================
# Edge cases
# =========================================================================


class TestEdgeCases:
    def test_operation_without_operation_id(self, tmp_path):
        """Operations without operationId should get auto-generated IDs."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "NoOpId", "version": "1.0.0"},
            "paths": {
                "/items": {
                    "get": {
                        "tags": ["items"],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        ops = extract_operations(spec)
        assert "items" in ops
        # Auto-generated operationId format: method_path
        assert ops["items"][0]["operation_id"] == "get_/items"

    def test_special_char_operation_id(self, tmp_path):
        """operationId with special characters is sanitized."""
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Special", "version": "1.0.0"},
            "paths": {
                "/x": {
                    "get": {
                        "tags": ["misc"],
                        "operationId": "get-item@v2!",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        ops = extract_operations(spec)
        method_name = sanitize_method_name(ops["misc"][0]["operation_id"])
        assert method_name.isidentifier()

    def test_multiple_content_types_prefers_json(self):
        """requestBody with multiple content types should prefer JSON."""
        spec = {
            "paths": {
                "/upload": {
                    "post": {
                        "tags": ["files"],
                        "operationId": "upload",
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {"schema": {"type": "object"}},
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                                },
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        }
        ops = extract_operations(spec)
        assert ops["files"][0]["body_schema"] is not None
        assert "properties" in ops["files"][0]["body_schema"]
