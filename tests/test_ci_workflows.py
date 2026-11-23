"""Contract & security checks for GitHub Actions CI, Release, and Problem CI workflows."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(rel_path: str) -> dict:
    raw = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw)
    assert isinstance(loaded, dict), f"{rel_path} must parse as a YAML mapping"
    return loaded


def test_ci_workflow_3_platform_matrix_and_least_privilege() -> None:
    wf = _load_yaml(".github/workflows/ci.yml")
    assert wf.get("permissions") == {"contents": "read"}
    jobs = wf.get("jobs", {})
    assert "lint" in jobs
    assert "typecheck" in jobs
    assert "plugin-contract" in jobs
    assert "test-unit" in jobs
    assert "test-integration" in jobs
    assert "test-packaging" in jobs
    assert "test-cross-platform" in jobs

    xplat_os = jobs["test-cross-platform"]["strategy"]["matrix"]["os"]
    assert set(xplat_os) == {"macos-latest", "windows-latest"}

    raw_ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "twine" not in raw_ci.lower()
    assert "pypi" not in raw_ci.lower()
    assert "write-all" not in raw_ci
    assert 'export PATH="/tmp/test-venv/bin:$PATH"' in raw_ci


def test_release_workflow_github_releases_no_pypi_and_sha256() -> None:
    rel_path = ".github/workflows/release.yml"
    wf = _load_yaml(rel_path)
    raw = (REPO_ROOT / rel_path).read_text(encoding="utf-8")

    # yaml.safe_load turns unquoted "on" key into boolean True in YAML 1.1
    triggers = wf.get("on") or wf.get(True)
    assert isinstance(triggers, dict)
    assert "v*" in triggers.get("push", {}).get("tags", [])

    assert wf.get("permissions") == {"contents": "read"}
    jobs = wf["jobs"]
    assert "build-and-verify" in jobs
    assert "publish-release" in jobs
    assert jobs["publish-release"]["permissions"] == {"contents": "write"}

    # Provenance & no-PyPI contract
    assert "twine" not in raw.lower()
    assert "pypi" not in raw.lower()
    assert "pypa/gh-action-pypi-publish" not in raw
    assert "sync_plugin_version.py --check" in raw
    assert "build_plugin_bundle.py" in raw
    for target in ("marketplace", "agy", "dsh"):
        assert target in raw
    assert "SHA256SUMS.txt" in raw
    assert "sha256sum -c SHA256SUMS.txt" in raw
    assert "gh release create" in raw
    assert 'export PATH="/tmp/release-smoke-venv/bin:$PATH"' in raw


def test_problem_ci_template_safe_and_complete() -> None:
    rel_path = "examples/ci/autocode-problem-audit.yml"
    wf = _load_yaml(rel_path)
    raw = (REPO_ROOT / rel_path).read_text(encoding="utf-8")

    assert wf.get("permissions") == {"contents": "read"}
    assert "--standalone" in raw
    assert "--format markdown" in raw
    assert "GITHUB_STEP_SUMMARY" in raw
    assert "twine" not in raw.lower()
    assert "pypi" not in raw.lower()
    # Guard against script injection via untrusted GitHub event context expressions inside run:
    assert not re.search(r"\$\{\{\s*github\.event\.", raw)
