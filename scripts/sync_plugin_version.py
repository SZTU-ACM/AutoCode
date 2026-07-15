#!/usr/bin/env python3
"""Sync the plugin / package version from ``pyproject.toml`` (single source).

``pyproject.toml`` is the authoritative version. This script propagates it to:

- ``.claude-plugin/plugin.json``  -> ``version``
- ``src/autocode_mcp/__init__.py`` -> ``__version__`` (single line literal)

Run it on release / build so the three never drift. No external deps beyond the
standard library.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (project floor) has no tomllib
    tomllib = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
INIT_PY = REPO_ROOT / "src" / "autocode_mcp" / "__init__.py"


def read_pyproject_version() -> str:
    if not PYPROJECT.is_file():
        raise SystemExit(f"pyproject.toml not found at {PYPROJECT}")
    text = PYPROJECT.read_text(encoding="utf-8")
    if tomllib is not None:
        version = tomllib.loads(text).get("project", {}).get("version")
    else:  # stdlib-only fallback for Python 3.10
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        version = match.group(1) if match else None
    if not version:
        raise SystemExit("version missing under [project] in pyproject.toml")
    return str(version)


def write_plugin_json(version: str) -> None:
    import json

    if not PLUGIN_JSON.is_file():
        raise SystemExit(f"plugin.json not found at {PLUGIN_JSON}")
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    old = plugin.get("version")
    plugin["version"] = version
    PLUGIN_JSON.write_text(json.dumps(plugin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"plugin.json: {old} -> {version}")


def write_init_version(version: str) -> None:
    if not INIT_PY.is_file():
        raise SystemExit(f"__init__.py not found at {INIT_PY}")
    text = INIT_PY.read_text(encoding="utf-8")
    pattern = re.compile(r'^__version__\s*=\s*["\'][^"\']*["\']', re.MULTILINE)
    if not pattern.search(text):
        raise SystemExit("__version__ assignment not found in __init__.py")
    new_text, count = pattern.subn(f'__version__ = "{version}"', text)
    if count != 1:
        raise SystemExit(f"expected exactly one __version__ assignment, found {count}")
    INIT_PY.write_text(new_text, encoding="utf-8")
    print(f"__init__.py: __version__ -> {version}")


def main() -> int:
    version = read_pyproject_version()
    write_plugin_json(version)
    write_init_version(version)
    print(f"Synced version {version} from pyproject.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
