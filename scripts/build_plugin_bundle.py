"""Build reproducible plugin-only trees for multi-host distribution.

The source repository remains the single source of truth. This builder exports
thin host-specific plugin bundles without vendoring tests, examples, OpenSpec
drafts, or local development state:

- ``marketplace`` (default): Claude Code + Codex dual-host marketplace bundle
- ``agy``: Native Antigravity (``agy``) plugin bundle (root ``plugin.json``,
  ``mcp_config.json``, ``rules/AGENTS.md``, ``skills/``, ``agents/``, ``assets/``)
- ``dsh``: DeepSeek Harness (``dsh``) plugin bundle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Literal

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (project floor) has no tomllib
    import tomli as tomllib  # type: ignore[no-redef]

TargetName = Literal["marketplace", "agy", "dsh"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "autocode"

# Legacy constant kept for backwards compatibility (marketplace target).
INCLUDE = (
    ".claude-plugin",
    ".codex-plugin",
    ".mcp.json",
    "settings.json",
    "hooks",
    "agents",
    "skills",
    "scripts",
    "src",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "TROUBLESHOOTING.md",
    "CHANGELOG.md",
    "LICENSE",
)

# Each target maps (source_rel_path, dest_rel_path).
TARGET_MAPPINGS: dict[TargetName, tuple[tuple[str, str], ...]] = {
    "marketplace": tuple((item, item) for item in INCLUDE),
    "agy": (
        (".antigravity-plugin/plugin.json", "plugin.json"),
        ("mcp_config.json", "mcp_config.json"),
        ("rules", "rules"),
        ("assets", "assets"),
        ("agents", "agents"),
        ("skills", "skills"),
        ("src", "src"),
        ("pyproject.toml", "pyproject.toml"),
        ("uv.lock", "uv.lock"),
        ("README.md", "README.md"),
        ("TROUBLESHOOTING.md", "TROUBLESHOOTING.md"),
        ("CHANGELOG.md", "CHANGELOG.md"),
        ("LICENSE", "LICENSE"),
    ),
    "dsh": (
        (".dsh-plugin", ".dsh-plugin"),
        ("rules", "rules"),
        ("skills", "skills"),
        ("src", "src"),
        ("pyproject.toml", "pyproject.toml"),
        ("uv.lock", "uv.lock"),
        ("README.md", "README.md"),
        ("TROUBLESHOOTING.md", "TROUBLESHOOTING.md"),
        ("CHANGELOG.md", "CHANGELOG.md"),
        ("LICENSE", "LICENSE"),
    ),
}

IGNORED_NAMES = frozenset(
    {"__pycache__", ".venv", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)


def _iter_source_files(path: Path):
    for child in sorted(path.rglob("*")):
        if not child.is_file():
            continue
        if any(part in IGNORED_NAMES for part in child.relative_to(path).parts):
            continue
        if child.suffix in {".pyc", ".pyo"}:
            continue
        yield child


def version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(b"file\0")
        digest.update(path.read_bytes())
    elif path.is_dir():
        digest.update(b"dir\0")
        for child in _iter_source_files(path):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(child.read_bytes())
            digest.update(b"\0")
    else:
        raise FileNotFoundError(f"cannot digest non-regular path: {path}")
    return digest.hexdigest()


def _copy_ignore(_: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES or name.endswith((".pyc", ".pyo"))
    }


def _target_mapping(target: TargetName) -> tuple[tuple[str, str], ...]:
    if target not in TARGET_MAPPINGS:
        raise ValueError(f"unsupported bundle target: {target!r}")
    return TARGET_MAPPINGS[target]


def source_fingerprint(target: TargetName = "marketplace") -> dict[str, str]:
    return {dest: file_digest(REPO_ROOT / src) for src, dest in _target_mapping(target)}


def _has_valid_release_marker(output: Path) -> bool:
    marker = output / ".autocode-release.json"
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("name") == "autocode"
        and isinstance(data.get("source_fingerprint"), dict)
    )


def remove_output(output: Path) -> None:
    if not output.exists() and not output.is_symlink():
        return
    if output.is_symlink() or not output.is_dir():
        raise ValueError(f"refusing to replace non-bundle output: {output}")

    resolved = output.resolve()
    repo_resolved = REPO_ROOT.resolve()
    if resolved == repo_resolved or resolved in repo_resolved.parents or (output / ".git").exists():
        raise ValueError(f"refusing to replace source repository or git working tree: {output}")

    if _has_valid_release_marker(output):
        shutil.rmtree(output)
        return

    # Marker-less legacy fallback strictly limited to an `autocode` directory
    # containing an AutoCode-specific host manifest directory.
    if output.name != "autocode":
        raise ValueError(f"refusing to replace non-bundle output: {output}")
    legacy_manifests = (
        output / ".claude-plugin" / "plugin.json",
        output / ".codex-plugin" / "plugin.json",
        output / ".dsh-plugin" / "package.json",
    )
    if not any(path.is_file() for path in legacy_manifests):
        raise ValueError(f"refusing to replace unrecognized directory: {output}")
    shutil.rmtree(output)


def build(output: Path, target: TargetName = "marketplace") -> None:
    mapping = _target_mapping(target)
    remove_output(output)
    output.mkdir(parents=True, exist_ok=True)
    for src_rel, dest_rel in mapping:
        source = REPO_ROOT / src_rel
        dest = output / dest_rel
        if source.is_dir():
            shutil.copytree(source, dest, ignore=_copy_ignore)
        elif source.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        else:
            raise FileNotFoundError(f"bundle input missing: {source}")
    provenance: dict[str, object] = {
        "name": "autocode",
        "version": version(),
        "source_repository": "https://github.com/SZTU-ACM/AutoCode",
        "source_fingerprint": source_fingerprint(target),
    }
    if target != "marketplace":
        provenance["target"] = target
    (output / ".autocode-release.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check(output: Path, target: TargetName = "marketplace") -> list[str]:
    if not output.is_dir():
        return [f"bundle directory missing: {output}"]
    errors: list[str] = []
    expected = source_fingerprint(target)
    for dest_rel, digest in expected.items():
        candidate = output / dest_rel
        if not candidate.exists():
            errors.append(f"bundle input missing: {dest_rel}")
        elif file_digest(candidate) != digest:
            errors.append(f"bundle drift: {dest_rel}")

    allowed_roots = {Path(dest_rel).parts[0] for dest_rel in expected} | {".autocode-release.json"}
    for entry in sorted(output.iterdir(), key=lambda p: p.name):
        if entry.name in IGNORED_NAMES:
            continue
        if entry.name not in allowed_roots:
            errors.append(f"unexpected bundle entry: {entry.name}")

    metadata_path = output / ".autocode-release.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        errors.append("bundle provenance missing or invalid")
    else:
        if not isinstance(metadata, dict):
            errors.append("bundle provenance missing or invalid")
        else:
            if metadata.get("version") != version():
                errors.append("bundle version differs from pyproject.toml")
            if metadata.get("source_fingerprint") != expected:
                errors.append("bundle provenance fingerprint differs from source")
            expected_target = None if target == "marketplace" else target
            if metadata.get("target") != expected_target:
                errors.append(
                    f"bundle target mismatch: expected {expected_target!r}, got {metadata.get('target')!r}"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--target",
        choices=("marketplace", "agy", "dsh"),
        default="marketplace",
        help="host bundle layout to build/check (default: marketplace)",
    )
    parser.add_argument("--check", action="store_true", help="check an existing bundle without rewriting it")
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    target: TargetName = args.target
    if args.check:
        errors = check(output, target=target)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print(f"Bundle is current ({target}): {output}")
        return 0
    build(output, target=target)
    print(f"Built {target} plugin bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
