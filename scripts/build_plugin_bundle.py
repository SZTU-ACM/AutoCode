"""Build a reproducible plugin-only tree for marketplace distribution.

The source repository remains authoritative.  A marketplace checkout can copy
the generated tree to ``plugins/autocode`` without vendoring tests, examples,
OpenSpec drafts, or local development state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (project floor) has no tomllib
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "autocode"
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
        digest.update(path.read_bytes())
    else:
        for child in _iter_source_files(path):
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            digest.update(child.read_bytes())
    return digest.hexdigest()


def _copy_ignore(_: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES or name.endswith((".pyc", ".pyo"))
    }


def source_fingerprint() -> dict[str, str]:
    return {item: file_digest(REPO_ROOT / item) for item in INCLUDE}


def remove_output(output: Path) -> None:
    if not output.exists():
        return
    if output.is_symlink() or not output.is_dir() or output.name != "autocode":
        raise ValueError(f"refusing to replace non-bundle output: {output}")
    marker = output / ".autocode-release.json"
    manifests = (
        output / ".claude-plugin" / "plugin.json",
        output / ".codex-plugin" / "plugin.json",
    )
    if not marker.is_file() and not any(path.is_file() for path in manifests):
        raise ValueError(f"refusing to replace unrecognized directory: {output}")
    shutil.rmtree(output)


def build(output: Path) -> None:
    remove_output(output)
    output.mkdir(parents=True, exist_ok=True)
    for item in INCLUDE:
        source = REPO_ROOT / item
        target = output / item
        if source.is_dir():
            shutil.copytree(source, target, ignore=_copy_ignore)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            raise FileNotFoundError(f"bundle input missing: {source}")
    provenance = {
        "name": "autocode",
        "version": version(),
        "source_repository": "https://github.com/SZTU-ACM/AutoCode",
        "source_fingerprint": source_fingerprint(),
    }
    (output / ".autocode-release.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check(output: Path) -> list[str]:
    if not output.is_dir():
        return [f"bundle directory missing: {output}"]
    errors: list[str] = []
    expected = source_fingerprint()
    for item, digest in expected.items():
        candidate = output / item
        if not candidate.exists():
            errors.append(f"bundle input missing: {item}")
        elif file_digest(candidate) != digest:
            errors.append(f"bundle drift: {item}")
    metadata_path = output / ".autocode-release.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append("bundle provenance missing or invalid")
    else:
        if metadata.get("version") != version():
            errors.append("bundle version differs from pyproject.toml")
        if metadata.get("source_fingerprint") != expected:
            errors.append("bundle provenance fingerprint differs from source")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="check an existing bundle without rewriting it")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if args.check:
        errors = check(output)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print(f"Bundle is current: {output}")
        return 0
    build(output)
    print(f"Built plugin bundle: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
