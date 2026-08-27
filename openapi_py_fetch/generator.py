"""
Pure Python OpenAPI client generator.

Generates thin Python API classes from an OpenAPI spec.  The generated classes
import their runtime (ApiClient, Configuration, exceptions) from the
``openapi_py_fetch`` package — no runtime code is duplicated into the output.

The generated classes have proper:
- inspect.signature() -> typed parameters
- get_type_hints() -> type annotations
- inspect.getdoc() -> docstrings from spec descriptions
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

from . import RUNTIME_EXPORTS
from .models import generate_models_module
from .naming import pascal_case, sanitize_method_name, sanitize_pep440_version, snake_case
from .schema_registry import SchemaRegistry

# ---------------------------------------------------------------------------
# Schema -> Python type mapping
# ---------------------------------------------------------------------------


def map_schema_to_python_type(schema: dict | None) -> str:
    """Map a schema to a Python type without spec context.

    The fallback for anything unrecognized is ``str``. Use
    :class:`~openapi_py_fetch.schema_registry.SchemaRegistry` instead whenever
    the spec is available: it resolves $ref, enums, unions and allOf, which this
    function cannot see.
    """
    if schema is None:
        return "str"

    schema_type = schema.get("type")
    nullable = schema.get("nullable", False)

    if schema_type == "string":
        base = "str"
    elif schema_type == "integer":
        base = "int"
    elif schema_type == "number":
        base = "float"
    elif schema_type == "boolean":
        base = "bool"
    elif schema_type == "array":
        item_type = map_schema_to_python_type(schema.get("items", {}))
        base = f"list[{item_type}]"
    elif schema_type == "object":
        base = "dict[str, Any]"
    else:
        base = "str"

    if nullable:
        return f"{base} | None"
    return base


# ---------------------------------------------------------------------------
# Operation extraction
# ---------------------------------------------------------------------------


def extract_operations(spec: dict) -> dict[str, list[dict]]:
    """Extract all operations grouped by tag."""
    operations_by_tag: dict[str, list[dict]] = {}

    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
            operation = path_item.get(method)
            if not operation or not isinstance(operation, dict):
                continue

            tags = operation.get("tags", ["default"])
            operation_id = operation.get("operationId", f"{method}_{path}")

            params = []
            for param in operation.get("parameters", []):
                params.append(
                    {
                        "name": param.get("name", ""),
                        "in": param.get("in", "query"),
                        "required": param.get("required", False),
                        "schema": param.get("schema", {}),
                        "description": param.get("description", ""),
                    }
                )

            request_body = operation.get("requestBody", {})
            body_schema = None
            if request_body:
                content = request_body.get("content", {})
                for ct in [
                    "application/json",
                    "application/x-www-form-urlencoded",
                    "multipart/form-data",
                ]:
                    if ct in content:
                        body_schema = content[ct].get("schema", {})
                        break

            responses = operation.get("responses", {})
            success_response = responses.get("200", responses.get("201", {}))
            response_schema = None
            if isinstance(success_response, dict):
                for ct, media in (success_response.get("content") or {}).items():
                    if "json" in ct and isinstance(media, dict):
                        response_schema = media.get("schema")
                        break

            op_info = {
                "operation_id": operation_id,
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary", ""),
                "description": operation.get("description", ""),
                "parameters": params,
                "body_schema": body_schema,
                "body_required": request_body.get("required", False),
                "response": success_response,
                "response_schema": response_schema,
            }

            for tag in tags:
                operations_by_tag.setdefault(tag, []).append(op_info)

    return operations_by_tag


# ---------------------------------------------------------------------------
# Code generation — methods & classes
# ---------------------------------------------------------------------------


def _build_call_api_args(op: dict) -> str:
    """Build the call_api kwargs dict literal for a generated method body."""
    path_params = []
    query_params = []
    header_params = []

    for param in op["parameters"]:
        pname = snake_case(param["name"])
        original_name = param["name"]
        location = param.get("in", "query")
        if location == "path":
            path_params.append((original_name, pname))
        elif location == "header":
            header_params.append((original_name, pname))
        else:
            query_params.append((original_name, pname))

    lines: list[str] = []

    if path_params:
        pairs = ", ".join(f'"{orig}": {py}' for orig, py in path_params)
        lines.append(f"        _path_params: dict[str, Any] = {{{pairs}}}")
    else:
        lines.append("        _path_params: dict[str, Any] = {}")

    lines.append("        _query_params: dict[str, Any] = {}")
    for orig, py in query_params:
        lines.append(f"        if {py} is not None:")
        lines.append(f'            _query_params["{orig}"] = {py}')

    lines.append("        _header_params: dict[str, str] = {}")
    for orig, py in header_params:
        lines.append(f"        if {py} is not None:")
        lines.append(f'            _header_params["{orig}"] = {py}')

    has_body = op.get("body_schema") is not None
    if has_body:
        lines.append("        _body = body")
    else:
        lines.append("        _body = None")

    return "\n".join(lines)


def _optional(annotation: str) -> str:
    """Make an annotation optional without stacking a second ``| None``."""
    if annotation == "Any" or "None" in [part.strip() for part in annotation.split("|")]:
        return annotation
    return f"{annotation} | None"


def generate_method(op: dict, registry: SchemaRegistry | None = None) -> str:
    """Generate a Python method for an API operation.

    With a registry, parameter and return annotations resolve $ref, enums and
    unions against the spec. Without one, types come from
    :func:`map_schema_to_python_type` and the return type is ``object``.
    """
    method_name = sanitize_method_name(op["operation_id"])
    http_method = op["method"].upper()
    path = op["path"]
    hint = pascal_case(sanitize_method_name(op["operation_id"]))

    def type_of(schema: dict | None, name_hint: str) -> str:
        if registry is None:
            return map_schema_to_python_type(schema)
        return registry.python_type(schema, name_hint)

    params: list[str] = ["self"]
    param_docs: list[str] = []
    required_params: list[tuple[str, str, str]] = []
    optional_params: list[tuple[str, str, str]] = []

    for param in op["parameters"]:
        pname = snake_case(param["name"])
        ptype = type_of(param.get("schema"), hint + pascal_case(pname))
        desc = param.get("description", f"{param['name']} parameter")
        if param.get("required", False):
            required_params.append((pname, ptype, desc))
        else:
            optional_params.append((pname, ptype, desc))

    if op.get("body_schema"):
        body_type = type_of(op["body_schema"], hint + "Request")
        if op.get("body_required", False):
            required_params.append(("body", body_type, "Request body"))
        else:
            optional_params.append(("body", body_type, "Request body"))

    for pname, ptype, desc in required_params:
        params.append(f"{pname}: {ptype}")
        param_docs.append(f":param {pname}: {desc}")

    for pname, ptype, desc in optional_params:
        params.append(f"{pname}: {_optional(ptype)} = None")
        param_docs.append(f":param {pname}: {desc} (optional)")

    params.append("**kwargs")

    summary = op.get("summary") or op.get("description") or f"{op['method']} {op['path']}"
    summary = summary.strip().split("\n")[0][:200]

    return_type = "object"
    if registry is not None:
        return_type = registry.python_type(op.get("response_schema"), hint + "Response")

    docstring_lines = [summary, "", f"{http_method} {path}", ""]
    docstring_lines.extend(param_docs)
    docstring_lines.append(":return: API response")
    docstring = "\n        ".join(docstring_lines)

    param_str = ", ".join(params)
    call_api_args = _build_call_api_args(op)
    http_info_type = "object" if return_type == "object" else f"tuple[{return_type}, int, dict[str, str]]"

    return f'''    def {method_name}({param_str}) -> {return_type}:
        """{docstring}
        """
{call_api_args}
        return self.api_client.call_api(
            "{path}", "{http_method}",
            path_params=_path_params,
            query_params=_query_params,
            header_params=_header_params,
            body=_body,
        )

    def {method_name}_with_http_info({param_str}) -> {http_info_type}:
        """{docstring}

        Returns tuple of (data, status_code, headers).
        """
{call_api_args}
        return self.api_client.call_api(
            "{path}", "{http_method}",
            path_params=_path_params,
            query_params=_query_params,
            header_params=_header_params,
            body=_body,
            _return_http_info=True,
        )
'''


def _references(code: str, name: str) -> bool:
    """Whether generated code uses *name* as a whole identifier."""
    return re.search(rf"\b{re.escape(name)}\b", code) is not None


def generate_api_class(
    tag: str,
    operations: list[dict],
    api_title: str,
    api_description: str,
    registry: SchemaRegistry | None = None,
) -> tuple[str, str, str]:
    """Generate a complete API class file for a tag.

    Returns (class_name, module_name, file_content).
    """
    class_name = pascal_case(tag) + "Api"
    module_name = snake_case(tag) + "_api"

    methods_code = ""
    for op in operations:
        methods_code += generate_method(op, registry) + "\n"

    typing_names = [name for name in ("Any", "Literal") if _references(methods_code, name)]
    typing_import = f"from typing import {', '.join(typing_names)}\n\n" if typing_names else ""

    model_import = ""
    if registry is not None:
        used = sorted(name for name in registry.models if _references(methods_code, name))
        if used:
            model_import = f"from openapi_client.models import {', '.join(used)}\n"

    content = f'''"""
    {api_title}

    {api_description}
    Generated by openapi-py-fetch.
"""

{typing_import}{model_import}from openapi_py_fetch import ApiClient


class {class_name}:
    """API class for {tag} operations.

    This class provides methods to interact with the {tag} endpoints
    of the {api_title} API.
    """

    def __init__(self, api_client: ApiClient | None = None) -> None:
        if api_client is None:
            api_client = ApiClient()
        self.api_client = api_client

{methods_code}'''

    return class_name, module_name, content


# ---------------------------------------------------------------------------
# Package generation (public API)
# ---------------------------------------------------------------------------


def enrich_spec_tags(spec: dict) -> list[str]:
    """Auto-discover tags used in paths but not declared in spec.tags."""
    declared = {t.get("name", "") for t in spec.get("tags", [])}
    discovered: list[str] = []

    for _path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
            op = path_item.get(method)
            if not op or not isinstance(op, dict):
                continue
            for tag in op.get("tags", []):
                if tag not in declared and tag not in discovered:
                    discovered.append(tag)
                    declared.add(tag)

    if discovered:
        tags_list = spec.setdefault("tags", [])
        for tag in discovered:
            tags_list.append({"name": tag})

    return discovered


def generate_client_package(
    spec: dict[str, Any],
    output_dir: Path,
    *,
    enrich_tags_fn: Any | None = None,
    tags: list[str] | None = None,
) -> bool:
    """Generate the complete openapi_client package from an OpenAPI spec.

    The generated package only contains thin API classes.  The runtime
    (ApiClient, Configuration, exceptions) is imported from the
    ``openapi_py_fetch`` package at runtime.

    Args:
        spec: Parsed OpenAPI specification dictionary.
        output_dir: Root of the generated_openapi directory
                    (the ``openapi_client`` package will be created inside it).
        enrich_tags_fn: Optional custom tag-enrichment function.  Defaults to
                        the built-in :func:`enrich_spec_tags`.
        tags: Optional list of tags to generate.  If ``None``, all tags are
              generated.

    Returns:
        ``True`` if generation + verification succeeded.
    """
    _enrich = enrich_tags_fn or enrich_spec_tags

    client_dir = output_dir / "openapi_client"
    api_dir = client_dir / "api"
    models_dir = client_dir / "models"

    if client_dir.exists():
        shutil.rmtree(client_dir)

    client_dir.mkdir(parents=True)
    api_dir.mkdir(parents=True)
    models_dir.mkdir(parents=True)

    info = spec.get("info", {})
    api_title = info.get("title", "Generated API")
    api_description = info.get("description", "")
    api_version = sanitize_pep440_version(info.get("version", "1.0.0"))

    discovered = _enrich(spec)
    if discovered:
        print(f"   [tags] Auto-discovered {len(discovered)} undeclared tag(s): {', '.join(discovered)}")

    operations_by_tag = extract_operations(spec)

    # Apply tag filter
    if tags:
        operations_by_tag = {tag: ops for tag, ops in operations_by_tag.items() if tag in tags}

    print(f"   Found {len(operations_by_tag)} API tag(s):")
    for tag, ops in sorted(operations_by_tag.items()):
        print(f"     {tag}: {len(ops)} operations")

    # Generate API classes. The registry collects every model the signatures
    # reference, so models must be written after the api modules.
    registry = SchemaRegistry(spec)
    api_classes: list[tuple[str, str]] = []
    for tag, operations in sorted(operations_by_tag.items()):
        class_name, module_name, content = generate_api_class(tag, operations, api_title, api_description, registry)
        api_classes.append((class_name, module_name))
        filepath = api_dir / f"{module_name}.py"
        filepath.write_text(content, encoding="utf-8")

    # API __init__.py
    api_init_lines = ["# flake8: noqa\n", "# import apis into api package\n"]
    for class_name, module_name in sorted(api_classes):
        api_init_lines.append(f"from openapi_client.api.{module_name} import {class_name}\n")
    (api_dir / "__init__.py").write_text("".join(api_init_lines), encoding="utf-8")

    # Models — one module, so cross-references never form an import cycle
    models_source, model_names = generate_models_module(registry, api_title)
    (models_dir / "__init__.py").write_text(models_source, encoding="utf-8")
    if model_names:
        print(f"   [ok] Generated {len(model_names)} models")

    # Main __init__.py — re-exports runtime from openapi_py_fetch
    init_lines = [f'"""\n{api_title}\n\n{api_description}\n"""\n\n']
    for class_name, module_name in sorted(api_classes):
        init_lines.append(f"from openapi_client.api.{module_name} import {class_name}  # noqa: F401\n")
    init_lines.append("from openapi_py_fetch import (  # noqa: F401\n")
    init_lines.extend(f"    {name},\n" for name in RUNTIME_EXPORTS)
    init_lines.append(")\n\n")
    init_lines.append(f'__version__ = "{api_version}"\n')
    (client_dir / "__init__.py").write_text("".join(init_lines), encoding="utf-8")

    # pyproject.toml — depends on openapi-py-fetch, NOT httpx directly
    pyproject = f'''[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "openapi-client"
version = "{api_version}"
description = "{api_title}"
requires-python = ">=3.11"
dependencies = ["openapi-py-fetch>=0.1"]
'''
    (output_dir / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    total_methods = sum(len(ops) for ops in operations_by_tag.values())
    print(f"   [ok] Generated {len(api_classes)} API classes ({total_methods} methods)")

    return _verify_package(output_dir)


def _verify_package(output_dir: Path) -> bool:
    """Verify the generated package can be imported and introspected."""
    import inspect

    if str(output_dir) not in sys.path:
        sys.path.insert(0, str(output_dir))

    # Force reimport in case of prior stale imports
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("openapi_client"):
            del sys.modules[mod_name]

    try:
        import openapi_client  # noqa: F811

        api_classes = [
            (name, getattr(openapi_client, name))
            for name in dir(openapi_client)
            if name.endswith("Api") and not name.startswith("_") and isinstance(getattr(openapi_client, name), type)
        ]

        if not api_classes:
            print("   [warn] Verification: no Api classes found")
            return False

        _cls_name, cls = api_classes[0]
        methods = [
            m
            for m in dir(cls)
            if not m.startswith("_") and not m.endswith("_with_http_info") and callable(getattr(cls, m))
        ]
        if methods:
            sig = inspect.signature(getattr(cls, methods[0]))
            doc = inspect.getdoc(getattr(cls, methods[0]))
            if sig and doc:
                print("   [ok] Introspection verified (signatures + docstrings OK)")

        return True

    except Exception as e:
        print(f"   [warn] Verification failed: {e}")
        return False
