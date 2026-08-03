"""Reproducible marketplace bundle tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def test_build_plugin_bundle_has_codex_manifest_and_provenance(tmp_path):
    path = Path("scripts/build_plugin_bundle.py")
    spec = importlib.util.spec_from_file_location("build_plugin_bundle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = tmp_path / "autocode"
    module.build(output)

    assert module.check(output) == []
    assert (output / ".codex-plugin" / "plugin.json").is_file()
    provenance = json.loads((output / ".autocode-release.json").read_text(encoding="utf-8"))
    assert provenance["source_repository"] == "https://github.com/SZTU-ACM/AutoCode"
    assert provenance["source_fingerprint"]


def test_build_plugin_bundle_refuses_unrecognized_existing_directory(tmp_path):
    path = Path("scripts/build_plugin_bundle.py")
    spec = importlib.util.spec_from_file_location("build_plugin_bundle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = tmp_path / "autocode"
    output.mkdir()
    (output / "user-data.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="unrecognized directory"):
        module.build(output)
