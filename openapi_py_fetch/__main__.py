#!/usr/bin/env python3
"""CLI entry point for openapi-py-fetch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .generator import generate_client_package


def main() -> int:
    """Generate a Python API client from an OpenAPI spec."""
    parser = argparse.ArgumentParser(
        prog="openapi-py-fetch",
        description="Generate Python API clients from OpenAPI 3.x specs.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("spec", help="Path to OpenAPI 3.x JSON spec")
    parser.add_argument(
        "output",
        nargs="?",
        default="generated_openapi",
        help="Output directory (default: generated_openapi)",
    )

    args = parser.parse_args()

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"\u274c Spec not found: {spec_path}")
        return 1

    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)

    info = spec.get("info", {})
    print(f"Spec: {info.get('title', '?')}")
    print(f"Paths: {len(spec.get('paths', {}))}")

    output_dir = Path(args.output)
    ok = generate_client_package(spec, output_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
