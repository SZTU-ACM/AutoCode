#!/usr/bin/env python3
"""Sync the plugin / package version from ``pyproject.toml`` (single source).

``pyproject.toml`` is the authoritative version. This script propagates it to:

- ``.claude-plugin/plugin.json``       -> ``version``
- ``.codex-plugin/plugin.json``        -> ``version``
- ``.dsh-plugin/package.json``         -> ``version``
- ``.antigravity-plugin/plugin.json``  -> ``version``
- ``src/autocode_mcp/__init__.py``     -> ``__version__`` (single line literal)

Run with ``--check`` in CI to fail if any target has drifted. No external deps
beyond the standard library.
"""

from __future__ import annotations

import argparse
import json
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
CODEX_PLUGIN_JSON = REPO_ROOT / ".codex-plugin" / "plugin.json"
DSH_PACKAGE_JSON = REPO_ROOT / ".dsh-plugin" / "package.json"
AGY_PLUGIN_JSON = REPO_ROOT / ".antigravity-plugin" / "plugin.json"
INIT_PY = REPO_ROOT / "src" / "autocode_mcp" / "__init__.py"

JSON_VERSION_TARGETS: tuple[tuple[str, Path], ...] = (
    (".claude-plugin/plugin.json", PLUGIN_JSON),
    (".codex-plugin/plugin.json", CODEX_PLUGIN_JSON),
    (".dsh-plugin/package.json", DSH_PACKAGE_JSON),
    (".antigravity-plugin/plugin.json", AGY_PLUGIN_JSON),
)


def _extract_project_version_fallback(text: str) -> str | None:
    """Stdlib-only Python 3.10 fallback scoped strictly to the ``[project]`` table."""
    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_project = line == "[project]"
            continue
        if in_project:
            match = re.match(r'^version\s*=\s*["\']([^"\']+)["\']', line)
            if match:
                return match.group(1)
    return None


def read_pyproject_version() -> str:
    if not PYPROJECT.is_file():
        raise SystemExit(f"pyproject.toml not found at {PYPROJECT}")
    text = PYPROJECT.read_text(encoding="utf-8")
    if tomllib is not None:
        version = tomllib.loads(text).get("project", {}).get("version")
    else:
        version = _extract_project_version_fallback(text)
    if not version:
        raise SystemExit("version missing under [project] in pyproject.toml")
    return str(version)


def _read_init_version() -> tuple[str | None, str | None]:
    if not INIT_PY.is_file():
        return None, f"src/autocode_mcp/__init__.py: missing at {INIT_PY}"
    try:
        text = INIT_PY.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return None, f"src/autocode_mcp/__init__.py: unreadable ({exc})"
    matches = re.findall(r'^__version__\s*=\s*["\']([^"\']*)["\']', text, re.MULTILINE)
    if len(matches) != 1:
        return None, f"src/autocode_mcp/__init__.py: expected 1 __version__ assignment, found {len(matches)}"
    return str(matches[0]), None


def write_json_manifest_version(label: str, path: Path, version: str) -> None:
    if not path.is_file():
        raise SystemExit(f"manifest not found at {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    old = manifest.get("version")
    manifest["version"] = version
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{label}: {old} -> {version}")


def write_plugin_json(version: str) -> None:
    write_json_manifest_version(".claude-plugin/plugin.json", PLUGIN_JSON, version)


def write_codex_plugin_json(version: str) -> None:
    write_json_manifest_version(".codex-plugin/plugin.json", CODEX_PLUGIN_JSON, version)


def write_dsh_package_json(version: str) -> None:
    write_json_manifest_version(".dsh-plugin/package.json", DSH_PACKAGE_JSON, version)


def write_agy_plugin_json(version: str) -> None:
    write_json_manifest_version(".antigravity-plugin/plugin.json", AGY_PLUGIN_JSON, version)


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


def check_versions(expected: str | None = None) -> list[str]:
    if expected is None:
        expected = read_pyproject_version()
    mismatches: list[str] = []
    for label, path in JSON_VERSION_TARGETS:
        if not path.is_file():
            mismatches.append(f"{label}: missing")
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            mismatches.append(f"{label}: unreadable ({exc})")
            continue
        actual = parsed.get("version") if isinstance(parsed, dict) else None
        if actual != expected:
            mismatches.append(f"{label}: {actual!r} != {expected!r}")
    init_ver, init_err = _read_init_version()
    if init_err is not None:
        mismatches.append(init_err)
    elif init_ver != expected:
        mismatches.append(f"src/autocode_mcp/__init__.py: {init_ver!r} != {expected!r}")
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify all host manifests and __init__.py match pyproject.toml without modifying files",
    )
    args = parser.parse_args(argv)

    version = read_pyproject_version()
    if args.check:
        mismatches = check_versions(expected=version)
        if mismatches:
            for item in mismatches:
                print(f"ERROR: version drift: {item}")
            return 1
        print(f"All host manifests match pyproject.toml ({version})")
        return 0

    for label, path in JSON_VERSION_TARGETS:
        write_json_manifest_version(label, path, version)
    write_init_version(version)
    print(f"Synced version {version} from pyproject.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
