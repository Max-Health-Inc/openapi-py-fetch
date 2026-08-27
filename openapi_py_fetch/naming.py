"""Identifier naming rules: python identifiers, method names, PEP 440 versions."""

from __future__ import annotations

import re


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
