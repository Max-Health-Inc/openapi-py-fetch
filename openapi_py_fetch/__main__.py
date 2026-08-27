#!/usr/bin/env python3
"""CLI entry point for openapi-py-fetch."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from . import __version__
from .generator import generate_client_package


def _load_spec(source: str) -> dict | None:
    """Load an OpenAPI spec from a file path or URL."""
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=30) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"\u274c Failed to fetch spec from URL: {exc}")
            return None

    spec_path = Path(source)
    if not spec_path.exists():
        print(f"\u274c Spec not found: {spec_path}")
        return None

    with open(spec_path, encoding="utf-8") as f:
        return json.load(f)


def _validate_spec(spec: dict) -> list[str]:
    """Validate basic OpenAPI structure. Returns list of error strings."""
    errors: list[str] = []
    if "openapi" not in spec and "swagger" not in spec:
        errors.append("Missing 'openapi' or 'swagger' key")
    version = spec.get("openapi", spec.get("swagger", ""))
    if version and not version.startswith(("2.", "3.")):
        errors.append(f"Unsupported version: {version}")
    if "paths" not in spec and "webhooks" not in spec:
        errors.append("Missing 'paths' or 'webhooks' key")
    return errors


def main() -> int:
    """Generate a Python API client from an OpenAPI spec."""
    parser = argparse.ArgumentParser(
        prog="openapi-py-fetch",
        description="Generate Python API clients from OpenAPI 3.x specs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("spec", help="Path or URL to OpenAPI 3.x JSON spec")
    parser.add_argument(
        "output",
        nargs="?",
        default="generated_openapi",
        help="Output directory (default: generated_openapi)",
    )
    parser.add_argument(
        "--tags",
        help="Comma-separated list of tags to generate (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate spec and preview output without writing files",
    )

    args = parser.parse_args()

    spec = _load_spec(args.spec)
    if spec is None:
        return 1

    # Validate
    errors = _validate_spec(spec)
    if errors:
        for err in errors:
            print(f"\u274c {err}")
        return 1

    info = spec.get("info", {})
    print(f"Spec: {info.get('title', '?')}")
    print(f"Paths: {len(spec.get('paths', {}))}")

    if args.dry_run:
        from .generator import enrich_spec_tags, extract_operations

        enrich_spec_tags(spec)
        ops = extract_operations(spec)
        tag_filter = [t.strip() for t in args.tags.split(",")] if args.tags else None
        for tag, tag_ops in sorted(ops.items()):
            if tag_filter and tag not in tag_filter:
                continue
            print(f"  {tag}: {len(tag_ops)} operations")
            for op in tag_ops:
                print(f"    {op['method']:6s} {op['path']}  ({op['operation_id']})")
        print("\n\u2705 Dry run complete — no files written.")
        return 0

    output_dir = Path(args.output)
    tag_filter = [t.strip() for t in args.tags.split(",")] if args.tags else None
    ok = generate_client_package(spec, output_dir, tags=tag_filter)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
