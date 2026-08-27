# openapi-py-fetch

[![PyPI](https://img.shields.io/pypi/v/openapi-py-fetch)](https://pypi.org/project/openapi-py-fetch/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/Max-Health-Inc/openapi-py-fetch/blob/main/LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Lightweight Python OpenAPI 3.x → Python httpx client generator and runtime.
Zero Java, zero npm — just Python 3.11+ and your OpenAPI spec.

A fast, single-package alternative to the 200MB+ Java `openapi-generator-cli`.
Generates typed Python API classes backed by a shared httpx runtime.

## Features

- **OpenAPI 2.0 & 3.x** — handles Swagger and OpenAPI specs
- **Shared runtime** — `ApiClient`, `Configuration`, and exceptions are a pip-installable library, not duplicated per project
- **Thin generated output** — only API classes are generated; they `import from openapi_py_fetch`
- **No runtime duplication** — unlike template-copy generators, the transport layer is maintained in one place
- **Drop-in compatible** — generated `openapi_client` package structure matches openapi-generator output
- **Typed models** — `$ref`, `allOf`, `oneOf`/`anyOf`, enums and nullable types resolve into TypedDicts, so responses and request bodies carry real types instead of `object`

## Installation

```bash
pip install openapi-py-fetch
```

## Usage

### As a CLI

```bash
# Generate client from local spec
openapi-py-fetch openapi.json ./generated_openapi

# Generate from URL
openapi-py-fetch https://petstore.swagger.io/v2/swagger.json ./generated_openapi

# Generate only specific tags
openapi-py-fetch openapi.json ./generated_openapi --tags pet,store

# Dry run — validate and preview without writing files
openapi-py-fetch openapi.json --dry-run

# Check version
openapi-py-fetch --version
```

### As a library (runtime)

```python
from openapi_py_fetch import ApiClient, Configuration, ApiException

config = Configuration(
    host="https://petstore.swagger.io/v2",
    api_key={"api_key": "special-key"},
)
client = ApiClient(configuration=config)
```

### As a library (generator)

```python
from openapi_py_fetch.generator import generate_client_package
from pathlib import Path
import json

with open("openapi.json") as f:
    spec = json.load(f)

generate_client_package(spec, Path("generated_openapi"))
```

## Generated Output Structure

```
generated_openapi/
├── pyproject.toml          # depends on openapi-py-fetch
└── openapi_client/
    ├── __init__.py          # re-exports runtime + API classes
    ├── api/
    │   ├── __init__.py
    │   ├── pet_api.py       # thin typed wrapper
    │   ├── store_api.py
    │   └── user_api.py
    └── models/
        └── __init__.py      # TypedDict per schema
```

## Typed Models

Object schemas become TypedDicts, collected in one `models` module:

```python
class Pet(TypedDict):
    """A pet."""

    id: str
    name: NotRequired[str]
    status: NotRequired[Literal["available", "pending", "sold"]]
```

TypedDict is the shape that matches what the runtime returns: `ApiClient.call_api`
hands back parsed JSON, so the annotation describes the dict a caller actually
receives, with no deserialization step and no runtime cost. A type checker still
catches a misspelled key or a wrong field type, and `get_type_hints()` resolves
every annotation for callers that introspect the generated client.

Resolved from the spec: `$ref` (including chains and self-references), `allOf`
(merged, with required fields propagated), `oneOf` and `anyOf` (unions), enums
(`Literal`), OpenAPI 3.1 nullable type arrays, `additionalProperties`, and
`format: binary` (`bytes`). Inline object schemas are named after their `title`
when they have one, otherwise after the operation they belong to.

Schema resolution is public API, for tools that read a spec rather than generate
from it:

```python
from openapi_py_fetch import SchemaRegistry

registry = SchemaRegistry(spec)
registry.python_type({"$ref": "#/components/schemas/Pet"})  # -> "Pet"
registry.models  # -> {"Pet": {...}}
```

## Comparison with openapi-ts-fetch

| | openapi-py-fetch | openapi-ts-fetch |
|---|---|---|
| Language | Python 3.11+ | TypeScript |
| HTTP transport | httpx | fetch |
| Runtime | Shared pip package | Copied template file |
| Generated output | Python classes | TypeScript classes |
| Models | TypedDicts | Interfaces + JSON converters |
| Response types | Resolved from the spec | Resolved from the spec |

## License

MIT — [Max Health Inc.](https://github.com/Max-Health-Inc)
