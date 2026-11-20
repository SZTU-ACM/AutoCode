"""Reproducible multi-host plugin bundle tests."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest


def _load_builder():
    path = Path("scripts/build_plugin_bundle.py")
    spec = importlib.util.spec_from_file_location("build_plugin_bundle", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handshake_mcp(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_sec: float = 30.0,
) -> dict:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    line_box: list[bytes] = []
    err_box: list[BaseException] = []

    def _read_line() -> None:
        try:
            assert proc.stdout is not None
            line_box.append(proc.stdout.readline())
        except BaseException as exc:  # pragma: no cover - defensive
            err_box.append(exc)

    try:
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        req = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agy-test", "version": "1.0.0"},
                },
            }
        )
        proc.stdin.write((req + "\n").encode("utf-8"))
        proc.stdin.flush()

        reader = threading.Thread(target=_read_line, daemon=True)
        reader.start()
        reader.join(timeout_sec)
        if reader.is_alive():
            proc.kill()
            reader.join(timeout=2)
            _, stderr_data = proc.communicate(timeout=5)
            raise AssertionError(
                f"MCP handshake timed out after {timeout_sec}s; stderr={stderr_data.decode('utf-8', errors='replace')!r}"
            )
        if err_box:
            raise err_box[0]
        raw_line = line_box[0] if line_box else b""
        if not raw_line:
            proc.terminate()
            _, stderr_data = proc.communicate(timeout=5)
            raise AssertionError(
                f"MCP server exited before responding; rc={proc.returncode}, stderr={stderr_data.decode('utf-8', errors='replace')!r}"
            )
        return json.loads(raw_line.decode("utf-8"))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def test_build_plugin_bundle_has_codex_manifest_and_provenance(tmp_path):
    module = _load_builder()

    output = tmp_path / "autocode"
    module.build(output)

    assert module.check(output) == []
    assert (output / ".codex-plugin" / "plugin.json").is_file()
    provenance = json.loads((output / ".autocode-release.json").read_text(encoding="utf-8"))
    assert provenance["source_repository"] == "https://github.com/SZTU-ACM/AutoCode"
    assert provenance["source_fingerprint"]


def test_build_plugin_bundle_agy_layout_drift_extra_and_target_checks(tmp_path):
    module = _load_builder()

    output = tmp_path / "autocode-agy"
    module.build(output, target="agy")

    # Idempotent in-place rebuild of custom-named bundle directory must succeed
    module.build(output, target="agy")
    assert module.check(output, target="agy") == []

    manifest = json.loads((output / "plugin.json").read_text(encoding="utf-8"))
    assert (output / manifest["logo"]).is_file()
    assert (output / "mcp_config.json").is_file()
    assert (output / "rules" / "AGENTS.md").is_file()
    assert (output / "skills" / "autocode-workflow" / "SKILL.md").is_file()
    assert (output / "agents" / "autocode-workflow.md").is_file()
    assert not (output / "hooks.json").exists()
    assert not (output / "tests").exists()

    provenance = json.loads((output / ".autocode-release.json").read_text(encoding="utf-8"))
    assert provenance["target"] == "agy"

    # Cross-target mismatch must fail check
    cross_errors = module.check(output, target="marketplace")
    assert any("target mismatch" in err or "bundle input missing" in err for err in cross_errors)

    # Extra unexpected root entry must be flagged
    stray = output / "hooks.json"
    stray.write_text("{}", encoding="utf-8")
    assert any("unexpected bundle entry: hooks.json" in err for err in module.check(output, target="agy"))
    stray.unlink()

    # Drift detection on modified bundled file
    orig_rules = (output / "rules" / "AGENTS.md").read_text(encoding="utf-8")
    (output / "rules" / "AGENTS.md").write_text("tampered\n", encoding="utf-8")
    assert any("bundle drift: rules" in err for err in module.check(output, target="agy"))
    (output / "rules" / "AGENTS.md").write_text(orig_rules, encoding="utf-8")
    assert module.check(output, target="agy") == []

    # Non-dict provenance JSON must fail cleanly (no AttributeError) and block unsafe rmtree
    (output / ".autocode-release.json").write_text("[]\n", encoding="utf-8")
    assert any("provenance missing or invalid" in err for err in module.check(output, target="agy"))
    with pytest.raises(ValueError, match="non-bundle output"):
        module.build(output, target="agy")


def test_build_plugin_bundle_dsh_and_custom_rebuild(tmp_path):
    module = _load_builder()

    for target, dirname in (("marketplace", "autocode-bundle"), ("dsh", "autocode-dsh")):
        output = tmp_path / dirname
        module.build(output, target=target)
        module.build(output, target=target)
        assert module.check(output, target=target) == []

    dsh_out = tmp_path / "autocode-dsh"
    assert (dsh_out / ".dsh-plugin" / "package.json").is_file()
    assert (dsh_out / ".dsh-plugin" / "cordis.patch.yml").is_file()
    assert (dsh_out / "skills" / "autocode-workflow" / "SKILL.md").is_file()


def test_antigravity_built_bundle_mcp_launches_with_env_and_placeholder(tmp_path):
    """Built AGY bundle (in a path with spaces) must launch autocode-mcp via PLUGIN_ROOT env or ${PLUGIN_ROOT} substitution."""
    module = _load_builder()

    bundle_dir = tmp_path / "agy plugin bundle"
    module.build(bundle_dir, target="agy")

    config = json.loads((bundle_dir / "mcp_config.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["autocode"]
    raw_cmd = [server["command"], *server["args"]]

    external_cwd = tmp_path / "unrelated-problem-workspace"
    external_cwd.mkdir()
    plugin_data = tmp_path / "plugin data"
    plugin_data.mkdir()

    # Mode 1: Host injects PLUGIN_ROOT & PLUGIN_DATA in env without expanding ${PLUGIN_ROOT} in args
    env_mode = {
        **os.environ,
        "PLUGIN_ROOT": str(bundle_dir),
        "PLUGIN_DATA": str(plugin_data),
        "PYTHONIOENCODING": "utf-8",
    }
    resp_env = _handshake_mcp(raw_cmd, cwd=external_cwd, env=env_mode)
    assert resp_env["result"]["serverInfo"]["name"] == "autocode-mcp"

    # Mode 2: Host expands ${PLUGIN_ROOT} in args without setting PLUGIN_ROOT in env
    expanded_cmd = [
        arg.replace("${PLUGIN_ROOT}", str(bundle_dir)) for arg in raw_cmd
    ]
    placeholder_env = {
        k: v for k, v in os.environ.items() if k != "PLUGIN_ROOT"
    }
    placeholder_env["PLUGIN_DATA"] = str(plugin_data)
    placeholder_env["PYTHONIOENCODING"] = "utf-8"
    resp_placeholder = _handshake_mcp(expanded_cmd, cwd=external_cwd, env=placeholder_env)
    assert resp_placeholder["result"]["serverInfo"]["name"] == "autocode-mcp"

    # Launching with --frozen + PLUGIN_DATA must not mutate or dirty the bundle
    assert module.check(bundle_dir, target="agy") == []


def test_build_plugin_bundle_safety_guards(tmp_path):
    module = _load_builder()

    # Unrecognized directory named autocode
    output = tmp_path / "autocode"
    output.mkdir()
    (output / "user-data.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="unrecognized directory"):
        module.build(output)

    # Arbitrary directory containing only a bare plugin.json (no release marker)
    bare_plugin = tmp_path / "other-plugin"
    bare_plugin.mkdir()
    (bare_plugin / "plugin.json").write_text('{"name":"other"}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-bundle output"):
        module.build(bare_plugin, target="agy")

    # Directory containing .git must never be wiped
    git_dir = tmp_path / "autocode-git"
    git_dir.mkdir()
    (git_dir / ".git").mkdir()
    (git_dir / ".autocode-release.json").write_text(
        json.dumps({"name": "autocode", "source_fingerprint": {}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="git working tree"):
        module.build(git_dir, target="agy")

    # Source repo root must never be wiped
    with pytest.raises(ValueError, match="source repository"):
        module.remove_output(module.REPO_ROOT)

    # Dangling symlink must be refused
    dangling = tmp_path / "dangling-link"
    try:
        dangling.symlink_to(tmp_path / "nonexistent-target")
    except OSError:
        pass
    else:
        with pytest.raises(ValueError, match="non-bundle output"):
            module.remove_output(dangling)
