from __future__ import annotations

import os
import tempfile

import pytest

from autocode_mcp.tools.complexity import ComplexityLevel
from autocode_mcp.tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool


@pytest.mark.asyncio
async def test_solution_audit_std_claimed_complexity_normalization():
    tool = SolutionAuditStdTool()
    code = """
int main() {
    int n;
    std::cin >> n;
    int sum = 0;
    for (int i = 0; i < n; i++) {
        sum += i;
    }
    std::cout << sum << std::endl;
    return 0;
}
"""
    result = await tool.execute(code=code, claimed_complexity="O(N)")
    assert result.success
    assert result.data["passed"] is True
    assert result.data["claimed_complexity"] == "O(n)"
    assert result.data["estimated_complexity"] == ComplexityLevel.LINEAR


@pytest.mark.asyncio
async def test_solution_audit_std_complexity_mismatch_warning():
    tool = SolutionAuditStdTool()
    code = """
int main() {
    int n;
    std::cin >> n;
    int sum = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            sum += i * j;
        }
    }
    std::cout << sum << std::endl;
    return 0;
}
"""
    result = await tool.execute(code=code, claimed_complexity="O(n)")
    assert result.success
    assert any(f["type"] == "complexity_mismatch" for f in result.data["findings"])


@pytest.mark.asyncio
async def test_solution_audit_std_pending_generator():
    tool = SolutionAuditStdTool()
    code = """
int main() {
    int n;
    std::cin >> n;
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        sol_dir = os.path.join(tmpdir, "solutions")
        os.makedirs(sol_dir, exist_ok=True)
        with open(os.path.join(sol_dir, "sol.cpp"), "w", encoding="utf-8") as f:
            f.write(code)

        result = await tool.execute(problem_dir=tmpdir, claimed_complexity="O(n)")
        assert result.success
        empirical = result.data.get("empirical_verification", {})
        assert empirical.get("status") == "pending_generator"


@pytest.mark.asyncio
async def test_solution_audit_brute_std_complexity():
    tool = SolutionAuditBruteTool()
    code = """
int main() {
    int n;
    std::cin >> n;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            sum += i * j;
        }
    }
    return 0;
}
"""
    result = await tool.execute(code=code, std_complexity="O(N^2)")
    assert result.success
    assert any(f["type"] == "same_order_as_std" for f in result.data["findings"])


@pytest.mark.asyncio
async def test_solution_audit_std_generator_error():
    from autocode_mcp.utils.compiler import get_exe_extension
    tool = SolutionAuditStdTool()
    code = "int main() { return 0; }"
    with tempfile.TemporaryDirectory() as tmpdir:
        sol_dir = os.path.join(tmpdir, "solutions")
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(sol_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)
        with open(os.path.join(sol_dir, "sol.cpp"), "w", encoding="utf-8") as f:
            f.write(code)

        exe_ext = get_exe_extension()
        sol_bin = os.path.join(sol_dir, f"sol{exe_ext}")
        with open(sol_bin, "w", encoding="utf-8") as f:
            f.write("binary")
        os.chmod(sol_bin, 0o755)

        gen_path = os.path.join(files_dir, f"gen{exe_ext}")
        with open(gen_path, "w", encoding="utf-8") as f:
            f.write("invalid binary")
        os.chmod(gen_path, 0o755)

        result = await tool.execute(problem_dir=tmpdir, claimed_complexity="O(n)")
        assert result.success
        assert any(f["type"] == "generator_error" for f in result.data["findings"])
