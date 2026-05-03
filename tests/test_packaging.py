"""测试打包配置、模板访问和基础功能。"""

import json
import os

import pytest

# ============== 基础功能测试（原 test_server.py） ==============


def test_import():
    """测试模块导入。"""
    from autocode_mcp import __version__

    assert __version__ == "1.0.3"


def test_tool_result():
    """测试 ToolResult 数据类。"""
    from autocode_mcp.tools.base import ToolResult

    result = ToolResult.ok(message="test")
    assert result.success is True
    assert result.error is None
    assert result.data["message"] == "test"

    result = ToolResult.fail("error message")
    assert result.success is False
    assert result.error == "error message"

    result = ToolResult.ok(value=123)
    d = result.to_dict()
    assert d["success"] is True
    assert d["data"]["value"] == 123


def test_all_tools_registered():
    """测试所有工具都能正确注册。"""
    from autocode_mcp.tools import (
        CheckerBuildTool,
        FileReadTool,
        FileSaveTool,
        GeneratorBuildTool,
        GeneratorRunTool,
        InteractorBuildTool,
        ProblemCleanupProcessesTool,
        ProblemCreateTool,
        ProblemGenerateTestsTool,
        ProblemPackPolygonTool,
        ProblemValidateTool,
        ProblemVerifyTestsTool,
        SolutionAnalyzeTool,
        SolutionAuditBruteTool,
        SolutionAuditStdTool,
        SolutionBuildTool,
        SolutionRunTool,
        StressTestRunTool,
        ValidatorBuildTool,
        ValidatorSelectTool,
    )

    tools = [
        FileReadTool(),
        FileSaveTool(),
        SolutionBuildTool(),
        SolutionRunTool(),
        SolutionAnalyzeTool(),
        SolutionAuditStdTool(),
        SolutionAuditBruteTool(),
        StressTestRunTool(),
        ProblemCreateTool(),
        ProblemGenerateTestsTool(),
        ProblemCleanupProcessesTool(),
        ProblemVerifyTestsTool(),
        ProblemPackPolygonTool(),
        ProblemValidateTool(),
        ValidatorBuildTool(),
        ValidatorSelectTool(),
        GeneratorBuildTool(),
        GeneratorRunTool(),
        CheckerBuildTool(),
        InteractorBuildTool(),
    ]

    expected_tool_names = {
        "file_read",
        "file_save",
        "solution_build",
        "solution_run",
        "solution_analyze",
        "solution_audit_std",
        "solution_audit_brute",
        "stress_test_run",
        "problem_create",
        "problem_generate_tests",
        "problem_cleanup_processes",
        "problem_verify_tests",
        "problem_pack_polygon",
        "problem_validate",
        "validator_build",
        "validator_select",
        "generator_build",
        "generator_run",
        "checker_build",
        "interactor_build",
    }
    assert {tool.name for tool in tools} == expected_tool_names

    for tool in tools:
        assert hasattr(tool, "name")
        assert hasattr(tool, "description")
        assert hasattr(tool, "input_schema")
        assert callable(tool.execute)


# ============== 模板和资源测试 ==============


def test_templates_in_package():
    """测试模板文件在包内可访问。"""
    from autocode_mcp import TEMPLATES_DIR

    assert os.path.exists(TEMPLATES_DIR), f"TEMPLATES_DIR not found: {TEMPLATES_DIR}"
    assert os.path.exists(os.path.join(TEMPLATES_DIR, "testlib.h"))


def test_resources_module_templates():
    """测试 resources 模块可以访问模板。"""
    from autocode_mcp.resources import get_template_path, list_templates

    templates = list_templates()
    assert "testlib.h" in templates

    path = get_template_path("testlib.h")
    assert path is not None
    assert os.path.exists(path)


def test_all_template_files_exist():
    """测试所有模板文件都存在。"""
    from autocode_mcp import TEMPLATES_DIR

    expected_templates = [
        "testlib.h",
        "validator_template.cpp",
        "generator_template.cpp",
        "checker_template.cpp",
        "interactor_template.cpp",
        "autocode.json",
        "tutorial_template.md",
    ]

    for template in expected_templates:
        path = os.path.join(TEMPLATES_DIR, template)
        assert os.path.exists(path), f"Template not found: {template}"


def test_all_prompts_exist():
    """测试所有声明的 prompt 都存在。"""
    from autocode_mcp.prompts import get_prompt, list_prompts

    prompts = list_prompts()
    assert len(prompts) == 6

    for name in prompts:
        content = get_prompt(name)
        assert content, f"Prompt '{name}' is empty"
        assert len(content) > 100, f"Prompt '{name}' seems too short"


def test_autocode_verify_reports_missing_manifest(monkeypatch, capsys, tmp_path):
    """CLI 在 manifest 缺失时输出稳定 JSON。"""
    from autocode_mcp.cli.verify import main

    monkeypatch.setattr("sys.argv", ["autocode-verify", str(tmp_path)])

    assert main() == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is False
    assert "not found" in parsed["error"]


def test_autocode_verify_reports_invalid_manifest(monkeypatch, capsys, tmp_path):
    """CLI 在 manifest 损坏时不应抛 traceback。"""
    from autocode_mcp.cli.verify import main

    (tmp_path / "autocode.json").write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(tmp_path)])

    assert main() == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is False
    assert "invalid autocode.json" in parsed["error"]


def test_autocode_verify_reports_unreadable_manifest(monkeypatch, capsys, tmp_path):
    """非法 UTF-8 的 autocode.json 应返回结构化失败。"""
    from autocode_mcp.cli.verify import main

    (tmp_path / "autocode.json").write_bytes(b"\xff\xfe\xfd")
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(tmp_path)])

    assert main() == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is False
    assert "invalid autocode.json" in parsed["error"]


def test_autocode_verify_accepts_valid_manifest(monkeypatch, capsys, tmp_path):
    """CLI 正常路径校验 statement/tutorial 路径。"""
    from autocode_mcp.cli.verify import main
    from autocode_mcp.workflow import default_manifest, save_manifest

    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "README.md").write_text("# Statement\n", encoding="utf-8")
    (statements_dir / "tutorial.md").write_text("# Tutorial\n", encoding="utf-8")
    save_manifest(str(tmp_path), default_manifest("CLI Test"))
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(tmp_path)])

    assert main() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is True
    assert parsed["missing_paths"] == []
    assert parsed.get("special_judge") is False


def test_autocode_verify_special_judge_exact_skips_checker_warn(monkeypatch, capsys, tmp_path):
    """仅 special_judge + exact 时不要求 checker 文件。"""
    from autocode_mcp.cli.verify import main

    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "README.md").write_text("# S\n", encoding="utf-8")
    (statements_dir / "tutorial.md").write_text("# T\n", encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "problem_name": "X",
        "interactive": False,
        "special_judge": True,
        "stress_comparison": "exact",
        "time_limit_ms": 1000,
        "memory_limit_mb": 256,
        "statement_path": "statements/README.md",
        "tutorial_path": "statements/tutorial.md",
        "solutions": [],
        "case_plan": [],
    }
    (tmp_path / "autocode.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(tmp_path)])

    assert main() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["spj_warnings"] == []


@pytest.mark.asyncio
async def test_problem_verify_tests_rejects_invalid_manifest(tmp_path):
    """problem_verify_tests 对非法 manifest 返回失败。"""
    from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool

    (tmp_path / "tests").mkdir()
    (tmp_path / "autocode.json").write_text(
        '{"schema_version":"1.0","problem_name":"m","interactive":false,'
        '"stress_comparison":"INVALID_ENUM"}',
        encoding="utf-8",
    )
    tool = ProblemVerifyTestsTool()
    result = await tool.execute(problem_dir=str(tmp_path), verify_types=["file_count"])
    assert not result.success
    err = (result.error or "").lower()
    assert "autocode" in err or "invalid" in err or "readable" in err


@pytest.mark.asyncio
async def test_problem_verify_tests_rejects_unreadable_manifest(tmp_path):
    """problem_verify_tests 对非法 UTF-8 的 manifest 返回失败。"""
    from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool

    (tmp_path / "tests").mkdir()
    (tmp_path / "autocode.json").write_bytes(b"\xff\xfe\xfd")
    tool = ProblemVerifyTestsTool()
    result = await tool.execute(problem_dir=str(tmp_path), verify_types=["file_count"])
    assert not result.success
    err = (result.error or "").lower()
    assert "autocode" in err or "invalid" in err or "readable" in err


def test_autocode_verify_spj_warns_without_checker(monkeypatch, capsys, tmp_path):
    """special_judge 时 CLI 报告 spj_warnings。"""
    from autocode_mcp.cli.verify import main

    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "README.md").write_text("# S\n", encoding="utf-8")
    (statements_dir / "tutorial.md").write_text("# T\n", encoding="utf-8")
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    manifest = {
        "schema_version": "1.0",
        "problem_name": "SPJ",
        "interactive": False,
        "special_judge": True,
        "stress_comparison": "checker",
        "time_limit_ms": 1000,
        "memory_limit_mb": 256,
        "statement_path": "statements/README.md",
        "tutorial_path": "statements/tutorial.md",
        "solutions": [],
        "case_plan": [],
    }
    (tmp_path / "autocode.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(tmp_path)])

    assert main() == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["special_judge"] is True
    assert parsed["spj_warnings"]


# ============== MCP 类型测试 ==============


@pytest.mark.asyncio
async def test_mcp_call_tool_result_type():
    """测试 call_tool 返回正确的 MCP 类型。"""
    from mcp.types import CallToolResult

    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    result = await call_tool("unknown_tool", {})
    assert isinstance(result, CallToolResult)
    assert result.isError is True


@pytest.mark.asyncio
async def test_mcp_call_tool_success_result():
    """测试 call_tool 成功时返回正确的结果。"""
    from mcp.types import CallToolResult

    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test content")

        result = await call_tool("file_read", {"path": test_file})
        assert isinstance(result, CallToolResult)
        assert result.isError is False
        assert result.structuredContent is not None


@pytest.mark.asyncio
async def test_mcp_get_prompt_result_type():
    """测试 get_prompt 返回正确的 MCP 类型。"""
    from mcp.types import GetPromptResult

    from autocode_mcp.server import get_prompt

    result = await get_prompt("validator")
    assert isinstance(result, GetPromptResult)
    assert len(result.messages) > 0

    result = await get_prompt("nonexistent_prompt")
    assert isinstance(result, GetPromptResult)
    assert "not found" in result.description.lower() or "error" in result.description.lower()


@pytest.mark.asyncio
async def test_mcp_read_resource_result_type():
    """测试 read_resource 返回正确的 MCP 类型。"""
    from mcp.types import ReadResourceResult

    from autocode_mcp.server import read_resource

    result = await read_resource("template://testlib.h")
    assert isinstance(result, ReadResourceResult)
    assert len(result.contents) > 0
    assert result.contents[0].text is not None

    result = await read_resource("template://nonexistent.txt")
    assert isinstance(result, ReadResourceResult)
    assert "not found" in result.contents[0].text.lower()


# ============== 工具边界情况测试 ==============


@pytest.mark.asyncio
async def test_interactor_reference_solution_not_found():
    """测试 interactor_build 在参考解不存在时报错而非静默跳过。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await call_tool(
            "interactor_build",
            {
                "problem_dir": tmpdir,
                "code": '#include "testlib.h"\nint main() { return 0; }',
                "reference_solution_path": os.path.join(tmpdir, "nonexistent.exe"),
            },
        )

        assert result.isError is True
        assert "Reference solution not found" in result.structuredContent.get("error", "")


@pytest.mark.asyncio
async def test_interactor_pass_rate_without_tests():
    """测试 interactor_build 没有测试时 pass_rate 为 0 而非 1.0。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await call_tool(
            "interactor_build",
            {
                "problem_dir": tmpdir,
                "code": '#include "testlib.h"\nint main() { return 0; }',
            },
        )

        assert result.isError is False
        data = result.structuredContent.get("data", {})
        assert data.get("pass_rate", 1.0) == 0.0


@pytest.mark.asyncio
async def test_checker_fail_verdict():
    """测试 checker_build 能区分 FAIL 和 WA。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        checker_code = '''
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    quitf(_fail, "Checker internal error");
    return 3;
}
'''
        result = await call_tool(
            "checker_build",
            {
                "problem_dir": tmpdir,
                "code": checker_code,
                "test_scenarios": [
                    {
                        "input": "1",
                        "contestant_output": "1",
                        "reference_output": "1",
                        "expected_verdict": "FAIL",
                    },
                ],
            },
        )

        assert result.isError is False
        test_results = result.structuredContent.get("test_results", [])
        if test_results:
            assert test_results[0].get("actual_verdict") == "FAIL"


# ============== source_path 参数测试 ==============


@pytest.mark.asyncio
async def test_solution_build_source_path():
    """测试 solution_build 使用 source_path 参数。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "solutions"))
        source_file = os.path.join(tmpdir, "solutions", "sol.cpp")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write('#include <iostream>\nint main() { std::cout << 42; return 0; }')

        result = await call_tool(
            "solution_build",
            {"problem_dir": tmpdir, "solution_type": "sol", "source_path": source_file},
        )
        assert result.isError is False


@pytest.mark.asyncio
async def test_solution_build_source_path_not_found():
    """测试 source_path 文件不存在时报错。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await call_tool(
            "solution_build",
            {
                "problem_dir": tmpdir,
                "solution_type": "sol",
                "source_path": os.path.join(tmpdir, "nonexistent.cpp"),
            },
        )
        assert result.isError is True
        assert "not found" in result.structuredContent.get("error", "").lower()


@pytest.mark.asyncio
async def test_solution_build_neither_code_nor_source_path():
    """测试既不提供 code 也不提供 source_path 时报错。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await call_tool(
            "solution_build",
            {"problem_dir": tmpdir, "solution_type": "sol"},
        )
        assert result.isError is True
        error = result.structuredContent.get("error", "").lower()
        assert "either" in error or "must be provided" in error


@pytest.mark.asyncio
async def test_solution_audit_accepts_source_path():
    """solution_audit_* 应与文档一致支持 source_path。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        source_file = os.path.join(tmpdir, "sol.cpp")
        with open(source_file, "w", encoding="utf-8") as f:
            f.write("int main() { for (int i = 0; i < 10; ++i) {} return 0; }")

        std_result = await call_tool(
            "solution_audit_std",
            {"problem_dir": tmpdir, "source_path": source_file, "constraints": {"n_max": 100}},
        )
        brute_result = await call_tool(
            "solution_audit_brute",
            {"problem_dir": tmpdir, "source_path": source_file, "constraints": {"n_max": 100}},
        )

        assert std_result.isError is False
        assert brute_result.isError is False


# ============== stress_test 错误诊断测试 ==============


@pytest.mark.asyncio
async def test_stress_test_generator_timeout_hint():
    """测试 generator 超时时返回特定提示和数据字段。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "files"))
        os.makedirs(os.path.join(tmpdir, "solutions"))

        gen_code = '#include "testlib.h"\nint main(int argc, char* argv[]) { while(true); return 0; }'
        gen_result = await call_tool("generator_build", {"problem_dir": tmpdir, "code": gen_code})
        if gen_result.isError:
            pytest.skip("Generator compilation failed (g++ not available)")

        simple_code = '#include <iostream>\nint main() { int x; std::cin >> x; std::cout << x; return 0; }'
        await call_tool("solution_build", {"problem_dir": tmpdir, "solution_type": "sol", "code": simple_code})
        await call_tool("solution_build", {"problem_dir": tmpdir, "solution_type": "brute", "code": simple_code})

        result = await call_tool("stress_test_run", {"problem_dir": tmpdir, "trials": 1, "timeout": 2})
        assert result.isError is True
        error_msg = result.structuredContent.get("error", "").lower()
        assert "generator failed" in error_msg
        data = result.structuredContent.get("data", {})
        assert "seed" in data
        assert "cmd_args" in data
