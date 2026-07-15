"""
Problem 验证工具组 - 验证题面样例和样例文件。
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Literal

from pydantic import ValidationError

from ..utils.checker_judge import checker_exe_path, run_testlib_checker
from ..utils.compiler import run_binary
from ..utils.platform import get_exe_extension
from ..workflow import load_manifest, manifest_uses_testlib_checker
from .base import Tool, ToolResult, input_schema_from_model
from .schemas import ProblemValidateInput


class ProblemValidateTool(Tool):
    """验证题面样例和样例文件。"""

    @property
    def name(self) -> str:
        return "problem_validate"

    @property
    def description(self) -> str:
        return """验证题面样例和样例文件的正确性。

        验证内容：
        - statement_samples: 验证题面中的样例答案是否正确（运行 sol）
        - sample_files: 验证 tests/ 目录下的样例文件是否与 sol 输出一致

        前置条件：
        1. 已运行 solution_build 构建 sol
        2. 建议先运行 stress_test_run 验证解法正确性

        建议下一步：
        - 如果验证通过，运行 problem_generate_tests 生成测试数据
        - 如果验证失败，检查题面样例答案或样例文件
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemValidateInput)

    async def execute(
        self,
        problem_dir: str,
        validate_types: list[Literal["statement_samples", "sample_files", "all"]] | None = None,
        statement_samples: list[dict] | None = None,
        tolerance: float = 1e-9,
        timeout: int = 30,
    ) -> ToolResult:
        """执行验证。"""
        if validate_types is None or "all" in validate_types:
            validate_types = ["statement_samples", "sample_files"]

        try:
            manifest_model = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")
        if manifest_model is not None and manifest_model.interactive:
            return await self._validate_interactive_problem(
                problem_dir=problem_dir,
                validate_types=validate_types,
                statement_samples=statement_samples,
                timeout=timeout,
            )

        # 检查 sol 是否存在
        exe_ext = get_exe_extension()
        sol_exe = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
        if not os.path.exists(sol_exe):
            sol_exe = os.path.join(problem_dir, f"sol{exe_ext}")
        if not os.path.exists(sol_exe):
            return ToolResult.fail("sol not found. Run solution_build first.")

        checker_bin = checker_exe_path(problem_dir, exe_ext)
        checker_for_samples = manifest_uses_testlib_checker(manifest_model) and os.path.isfile(
            checker_bin
        )

        results = {}
        all_passed = True

        # 验证题面样例
        if "statement_samples" in validate_types:
            samples = statement_samples
            if samples is None:
                # 从 README.md 自动提取
                readme_path = os.path.join(problem_dir, "statements", "README.md")
                if not os.path.exists(readme_path):
                    readme_path = os.path.join(problem_dir, "README.md")
                if os.path.exists(readme_path):
                    samples = self._extract_samples_from_readme(readme_path)

            if samples:
                result = await self._validate_statement_samples(
                    sol_exe,
                    samples,
                    tolerance,
                    timeout,
                    problem_dir=problem_dir,
                    checker_exe=checker_bin if checker_for_samples else None,
                )
                results["statement_samples"] = result
                if not result.get("passed", 0) == result.get("total", 0):
                    all_passed = False
            else:
                results["statement_samples"] = {
                    "validated": False,
                    "message": "No statement samples found or provided",
                }
                all_passed = False  # 没有样例时验证失败

        # 验证样例文件
        if "sample_files" in validate_types:
            tests_dir = os.path.join(problem_dir, "tests")
            if os.path.exists(tests_dir):
                result = await self._validate_sample_files(
                    sol_exe,
                    tests_dir,
                    tolerance,
                    timeout,
                    problem_dir=problem_dir,
                    checker_exe=checker_bin if checker_for_samples else None,
                )
                results["sample_files"] = result
                if not result.get("passed", 0) == result.get("total", 0):
                    all_passed = False
            else:
                results["sample_files"] = {
                    "validated": False,
                    "message": "tests/ directory not found",
                }
                all_passed = False  # 没有样例文件时验证失败

        if all_passed:
            return ToolResult.ok(
                **results,
                message="All validations passed",
            )
        else:
            return ToolResult.fail(
                "Validation failed",
                **results,
            )

    async def _validate_interactive_problem(
        self,
        problem_dir: str,
        validate_types: list[Literal["statement_samples", "sample_files", "all"]],
        statement_samples: list[dict] | None,
        timeout: int,
    ) -> ToolResult:
        """验证交互题协议骨架，而不是把 transcript 当作静态 stdin/stdout 样例。"""
        readme_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(readme_path):
            readme_path = os.path.join(problem_dir, "README.md")
        if not os.path.exists(readme_path):
            return ToolResult.fail(
                "interactive statement missing",
                statement_samples={"validated": False, "message": "statement file missing"},
                sample_files={"validated": False, "message": "statement file missing"},
            )

        with open(readme_path, encoding="utf-8") as f:
            content = f.read()

        protocol_checks = {
            "interaction_protocol_section": "## 交互协议" in content or "interaction protocol" in content.lower(),
            "flush": "flush" in content.lower() or "刷新" in content,
            "query_limit": (
                "查询" in content
                and ("次数" in content or "上限" in content or "最多" in content or "limit" in content.lower())
            ),
            "final_answer": ("最终答案" in content or "final answer" in content.lower()),
            "judge_response": ("judge" in content.lower() or "交互器返回" in content or "返回" in content),
            "transcript": ("judge:" in content.lower() and "contestant:" in content.lower())
            or ("judge" in content.lower() and "选手" in content),
        }
        passed = all(protocol_checks.values())
        statement_result = {
            "validated": True,
            "passed": 1 if passed else 0,
            "failed": 0 if passed else 1,
            "total": 1,
            "details": [
                {
                    "interactive": True,
                    "checks": protocol_checks,
                    "passed": passed,
                }
            ],
            "mode": "interactive_protocol",
        }

        sample_files_result = {
            "validated": True,
            "passed": 1,
            "failed": 0,
            "total": 1,
            "details": [
                {
                    "interactive": True,
                    "message": "transcript samples are validated as protocol text, not static stdin/stdout",
                }
            ],
            "mode": "interactive_protocol",
        }

        results = {
            "statement_samples": statement_result,
            "sample_files": sample_files_result,
            "interactive_protocol": {
                "validated": True,
                "passed": passed,
                "checks": protocol_checks,
            },
        }
        if passed:
            return ToolResult.ok(**results, message="Interactive protocol validation passed")
        return ToolResult.fail("Interactive protocol validation failed", **results)

    async def _validate_statement_samples(
        self,
        sol_exe: str,
        samples: list[dict],
        tolerance: float,
        timeout: int,
        *,
        problem_dir: str | None = None,
        checker_exe: str | None = None,
    ) -> dict:
        """验证题面样例。SPJ 且提供 checker 时用 checker(input, sol_out, expected)。"""
        details = []
        passed = 0
        failed = 0

        for i, sample in enumerate(samples, 1):
            input_data = sample.get("input", "")
            expected = sample.get("expected_output", "")

            # 运行 sol
            result = await run_binary(sol_exe, input_data, timeout=timeout)
            if not result.success:
                details.append({
                    "index": i,
                    "input": input_data,
                    "expected": expected,
                    "actual": None,
                    "passed": False,
                    "error": result.error or "Execution failed",
                })
                failed += 1
                continue

            actual = result.stdout.strip()
            expected_stripped = expected.strip()

            if checker_exe and problem_dir:
                with tempfile.TemporaryDirectory(dir=problem_dir) as tmp:
                    in_path = os.path.join(tmp, "input.txt")
                    out_path = os.path.join(tmp, "output.txt")
                    ans_path = os.path.join(tmp, "answer.txt")
                    with open(in_path, "w", encoding="utf-8", newline="\n") as inf:
                        inf.write(input_data)
                    with open(out_path, "w", encoding="utf-8", newline="\n") as ouf:
                        ouf.write(result.stdout)
                    with open(ans_path, "w", encoding="utf-8", newline="\n") as anf:
                        anf.write(expected)
                    verdict, _ = await run_testlib_checker(
                        checker_exe, in_path, out_path, ans_path, timeout=timeout
                    )
                    is_passed = verdict == "AC"
            else:
                is_passed = self._compare_output(actual, expected_stripped, tolerance)

            details.append({
                "index": i,
                "input": input_data,
                "expected": expected_stripped,
                "actual": actual,
                "passed": is_passed,
            })
            if is_passed:
                passed += 1
            else:
                failed += 1

        out: dict = {
            "validated": True,
            "passed": passed,
            "failed": failed,
            "total": len(samples),
            "details": details,
        }
        if checker_exe:
            out["mode"] = "checker"
        return out

    async def _validate_sample_files(
        self,
        sol_exe: str,
        tests_dir: str,
        tolerance: float,
        timeout: int,
        *,
        problem_dir: str | None = None,
        checker_exe: str | None = None,
    ) -> dict:
        """验证样例文件。"""
        # 找到所有 .in 文件
        in_files = sorted([f for f in os.listdir(tests_dir) if f.endswith(".in")])
        if not in_files:
            return {
                "validated": False,
                "message": "No .in files found in tests/",
            }

        details = []
        passed = 0
        failed = 0

        for in_file in in_files:
            in_path = os.path.join(tests_dir, in_file)
            # 对应的 .ans 文件
            base = in_file[:-3]  # 去掉 ".in"
            ans_file = base + ".ans"
            ans_path = os.path.join(tests_dir, ans_file)

            # 读取输入
            with open(in_path, encoding="utf-8") as f:
                input_data = f.read()

            # 运行 sol
            result = await run_binary(sol_exe, input_data, timeout=timeout)
            if not result.success:
                details.append({
                    "file": in_file,
                    "expected_file": ans_file if os.path.exists(ans_path) else None,
                    "passed": False,
                    "error": result.error or "Execution failed",
                })
                failed += 1
                continue

            actual = result.stdout.strip()

            # 检查 .ans 文件是否存在
            if not os.path.exists(ans_path):
                details.append({
                    "file": in_file,
                    "expected_file": None,
                    "actual": actual,
                    "passed": False,
                    "error": f"Missing .ans file: {ans_file}",
                })
                failed += 1
                continue

            # 读取期望输出
            with open(ans_path, encoding="utf-8") as f:
                expected = f.read().strip()

            if checker_exe and problem_dir:
                with tempfile.TemporaryDirectory(dir=problem_dir) as tmp:
                    out_path = os.path.join(tmp, "output.txt")
                    with open(out_path, "w", encoding="utf-8", newline="\n") as ouf:
                        ouf.write(result.stdout)
                    verdict, _ = await run_testlib_checker(
                        checker_exe, in_path, out_path, ans_path, timeout=timeout
                    )
                    is_passed = verdict == "AC"
            else:
                is_passed = self._compare_output(actual, expected, tolerance)
            details.append({
                "file": in_file,
                "expected_file": ans_file,
                "actual": actual,
                "expected": expected,
                "passed": is_passed,
            })
            if is_passed:
                passed += 1
            else:
                failed += 1

        return {
            "validated": True,
            "passed": passed,
            "failed": failed,
            "total": len(in_files),
            "details": details,
        }

    def _compare_output(self, actual: str, expected: str, tolerance: float) -> bool:
        """比较输出。"""
        # 精确比较
        if actual == expected:
            return True

        # 忽略空白差异（逐行比较）
        actual_lines = [line.rstrip() for line in actual.split("\n")]
        expected_lines = [line.rstrip() for line in expected.split("\n")]
        if actual_lines == expected_lines:
            return True

        # 忽略所有空白差异（token 比较）
        actual_tokens = actual.split()
        expected_tokens = expected.split()
        if actual_tokens == expected_tokens:
            return True

        # 浮点数比较
        try:
            if len(actual_tokens) == len(expected_tokens):
                actual_nums = [float(x) for x in actual_tokens]
                expected_nums = [float(x) for x in expected_tokens]
                return all(abs(a - e) < tolerance for a, e in zip(actual_nums, expected_nums, strict=False))
        except ValueError:
            pass

        return False

    def _extract_samples_from_readme(self, readme_path: str) -> list[dict]:
        """从 README.md 提取样例。

        支持的格式：
        1. Markdown code block 格式：
           **样例输入 1**
           ```text
           5
           3 -5 2 -8 4
           ```
           **样例输出 1**
           ```text
           2
           ```

        2. 中文标记格式：
           样例输入：
           5
           3 -5 2 -8 4
           样例输出：
           2

        3. 英文标记格式：
           Sample Input 1:
           ...
           Sample Output 1:
           ...
        """
        with open(readme_path, encoding="utf-8") as f:
            content = f.read()

        samples = []

        # 方法 1: 提取所有代码块，按输入/输出标记配对
        # 匹配模式：标记 + 代码块
        pattern = r"(?:样例输入|Sample\s*Input|输入样例|Input\s*Sample)[^\n]*\n+```[^\n]*\n(.*?)```"
        input_blocks = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)

        pattern = r"(?:样例输出|Sample\s*Output|输出样例|Output\s*Sample)[^\n]*\n+```[^\n]*\n(.*?)```"
        output_blocks = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)

        # 配对
        for _i, (inp, out) in enumerate(zip(input_blocks, output_blocks, strict=False)):
            samples.append({
                "input": inp.strip(),
                "expected_output": out.strip(),
            })

        # 方法 2: 如果方法 1 没找到，尝试直接提取代码块配对
        if not samples:
            # 提取所有代码块
            code_blocks = re.findall(r"```[^\n]*\n(.*?)```", content, re.DOTALL)
            # 假设输入/输出交替出现
            for i in range(0, len(code_blocks) - 1, 2):
                samples.append({
                    "input": code_blocks[i].strip(),
                    "expected_output": code_blocks[i + 1].strip(),
                })

        # 方法 3: 支持纯文本格式（无代码块）
        # 格式：样例输入：\n内容\n样例输出：\n内容
        if not samples:
            # 使用更宽松的模式匹配纯文本格式
            # 匹配 "样例输入" 或 "Sample Input" 后的内容，直到下一个标签或文档结束
            input_pattern = r"(?:样例输入|Sample\s*Input)[^\n]*[:：]?\s*\n(.*?)(?=(?:样例输出|Sample\s*Output)|$)"
            output_pattern = r"(?:样例输出|Sample\s*Output)[^\n]*[:：]?\s*\n(.*?)(?=(?:样例输入|Sample\s*Input)|$)"

            inputs = re.findall(input_pattern, content, re.DOTALL | re.IGNORECASE)
            outputs = re.findall(output_pattern, content, re.DOTALL | re.IGNORECASE)

            for inp, out in zip(inputs, outputs, strict=False):
                samples.append({
                    "input": inp.strip(),
                    "expected_output": out.strip(),
                })

        return samples
