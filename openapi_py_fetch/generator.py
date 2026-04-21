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

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def snake_case(name: str) -> str:
    """Convert a string to snake_case."""
    name = name.replace("-", "_")
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def pascal_case(name: str) -> str:
    """Convert a string to PascalCase."""
    parts = re.split(r"[-_\s]+", name)
    return "".join(p.capitalize() for p in parts if p)


def sanitize_method_name(operation_id: str) -> str:
    """Convert operationId to a valid Python method name."""
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "", operation_id)
    result = snake_case(clean)
    if result and result[0].isdigit():
        result = "op_" + result
    return result


def sanitize_pep440_version(version: str) -> str:
    """Coerce an arbitrary version string into PEP 440 format."""
    m = re.match(r"(\d+(?:\.\d+)*)", version)
    if not m:
        return "0.0.0"
    base = m.group(1)
    rest = version[m.end() :]

    pre = re.match(r"[\-.]?(alpha|beta|rc|dev)(.*)", rest, re.IGNORECASE)
    if pre:
        tag = pre.group(1).lower()
        num_match = re.search(r"(\d+)", pre.group(2))
        num = num_match.group(1) if num_match else "0"
        mapping = {"alpha": "a", "beta": "b", "rc": "rc", "dev": ".dev"}
        suffix = mapping.get(tag, "a")
        return f"{base}{suffix}{num}"

    return base


# ---------------------------------------------------------------------------
# Schema -> Python type mapping
# ---------------------------------------------------------------------------


def map_schema_to_python_type(schema: dict | None) -> str:
    """Map an OpenAPI schema to a Python type annotation string."""
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
        lines.append(f"        _path_params = {{{pairs}}}")
    else:
        lines.append("        _path_params = {}")

    if query_params:
        lines.append("        _query_params = {}")
        for orig, py in query_params:
            lines.append(f"        if {py} is not None:")
            lines.append(f'            _query_params["{orig}"] = {py}')
    else:
        lines.append("        _query_params = {}")

    if header_params:
        lines.append("        _header_params = {}")
        for orig, py in header_params:
            lines.append(f"        if {py} is not None:")
            lines.append(f'            _header_params["{orig}"] = {py}')
    else:
        lines.append("        _header_params = {}")

    has_body = op.get("body_schema") is not None
    if has_body:
        lines.append("        _body = body")
    else:
        lines.append("        _body = None")

    return "\n".join(lines)


def generate_method(op: dict) -> str:
    """Generate a Python method for an API operation."""
    method_name = sanitize_method_name(op["operation_id"])
    http_method = op["method"].upper()
    path = op["path"]

    params: list[str] = ["self"]
    param_docs: list[str] = []
    required_params: list[tuple[str, str, str]] = []
    optional_params: list[tuple[str, str, str]] = []

    for param in op["parameters"]:
        pname = snake_case(param["name"])
        ptype = map_schema_to_python_type(param.get("schema"))
        desc = param.get("description", f"{param['name']} parameter")
        if param.get("required", False):
            required_params.append((pname, ptype, desc))
        else:
            optional_params.append((pname, ptype, desc))

    if op.get("body_schema"):
        body_type = map_schema_to_python_type(op["body_schema"])
        if op.get("body_required", False):
            required_params.append(("body", body_type, "Request body"))
        else:
            optional_params.append(("body", body_type, "Request body"))

    for pname, ptype, desc in required_params:
        params.append(f"{pname}: {ptype}")
        param_docs.append(f":param {pname}: {desc}")

    for pname, ptype, desc in optional_params:
        params.append(f"{pname}: {ptype} | None = None")
        param_docs.append(f":param {pname}: {desc} (optional)")

    params.append("**kwargs")

    summary = op.get("summary") or op.get("description") or f"{op['method']} {op['path']}"
    summary = summary.strip().split("\n")[0][:200]

    docstring_lines = [summary, "", f"{http_method} {path}", ""]
    docstring_lines.extend(param_docs)
    docstring_lines.append(":return: API response")
    docstring = "\n        ".join(docstring_lines)

    param_str = ", ".join(params)
    call_api_args = _build_call_api_args(op)

    return f'''    def {method_name}({param_str}) -> object:
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

    def {method_name}_with_http_info({param_str}) -> object:
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


def generate_api_class(
    tag: str,
    operations: list[dict],
    api_title: str,
    api_description: str,
) -> tuple[str, str, str]:
    """Generate a complete API class file for a tag.

    Returns (class_name, module_name, file_content).
    """
    class_name = pascal_case(tag) + "Api"
    module_name = snake_case(tag) + "_api"

    methods_code = ""
    for op in operations:
        methods_code += generate_method(op) + "\n"

    content = f'''# coding: utf-8

"""
    {api_title}

    {api_description}
    Generated by openapi-py-fetch.
"""

from typing import Any

from openapi_py_fetch import ApiClient


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
        print(
            f"   \U0001f3f7\ufe0f  Auto-discovered {len(discovered)} "
            f"undeclared tag(s): {', '.join(discovered)}"
        )

    operations_by_tag = extract_operations(spec)

    print(f"   Found {len(operations_by_tag)} API tag(s):")
    for tag, ops in sorted(operations_by_tag.items()):
        print(f"     {tag}: {len(ops)} operations")

    # Generate API classes
    api_classes: list[tuple[str, str]] = []
    for tag, operations in sorted(operations_by_tag.items()):
        class_name, module_name, content = generate_api_class(
            tag, operations, api_title, api_description
        )
        api_classes.append((class_name, module_name))
        filepath = api_dir / f"{module_name}.py"
        filepath.write_text(content, encoding="utf-8")

    # API __init__.py
    api_init_lines = ["# flake8: noqa\n", "# import apis into api package\n"]
    for class_name, module_name in sorted(api_classes):
        api_init_lines.append(
            f"from openapi_client.api.{module_name} import {class_name}\n"
        )
    (api_dir / "__init__.py").write_text("".join(api_init_lines), encoding="utf-8")

    # Models __init__.py (stub — no model classes generated)
    (models_dir / "__init__.py").write_text(
        "# flake8: noqa\n\n# No model classes generated (schemas are inline)\n",
        encoding="utf-8",
    )

    # Main __init__.py — re-exports runtime from openapi_py_fetch
    init_lines = [
        f'"""\n{api_title}\n\n{api_description}\n"""\n\n',
        f'__version__ = "{api_version}"\n\n',
        "# Runtime — imported from shared openapi_py_fetch package\n",
        "from openapi_py_fetch import ApiClient  # noqa: F401\n",
        "from openapi_py_fetch import ApiResponse  # noqa: F401\n",
        "from openapi_py_fetch import Configuration  # noqa: F401\n",
        "from openapi_py_fetch import (  # noqa: F401\n",
        "    OpenApiException,\n",
        "    ApiTypeError,\n",
        "    ApiValueError,\n",
        "    ApiKeyError,\n",
        "    ApiAttributeError,\n",
        "    ApiException,\n",
        ")\n\n",
        "# API classes\n",
    ]
    for class_name, module_name in sorted(api_classes):
        init_lines.append(
            f"from openapi_client.api.{module_name} import {class_name}  # noqa: F401\n"
        )
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
    print(f"   \u2705 Generated {len(api_classes)} API classes ({total_methods} methods)")

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
            if name.endswith("Api")
            and not name.startswith("_")
            and isinstance(getattr(openapi_client, name), type)
        ]

        if not api_classes:
            print("   \u26a0\ufe0f  Verification: no Api classes found")
            return False

        _cls_name, cls = api_classes[0]
        methods = [
            m
            for m in dir(cls)
            if not m.startswith("_")
            and not m.endswith("_with_http_info")
            and callable(getattr(cls, m))
        ]
        if methods:
            sig = inspect.signature(getattr(cls, methods[0]))
            doc = inspect.getdoc(getattr(cls, methods[0]))
            if sig and doc:
                print("   \u2705 Introspection verified (signatures + docstrings OK)")

        return True

    except Exception as e:
        print(f"   \u26a0\ufe0f  Verification failed: {e}")
        return False
