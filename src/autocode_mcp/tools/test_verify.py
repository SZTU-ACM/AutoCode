"""
Test Verification 工具 - 验证生成的测试数据。

检查文件完整性、答案一致性、约束覆盖等。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..utils.compiler import run_binary
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult

_LIMIT_STRATEGY_TYPES = frozenset({"3", "4"})
_TEST_MANIFEST_FILENAME = ".autocode_tests_manifest.json"


class ProblemVerifyTestsTool(Tool):
    """验证生成的测试数据。"""

    @property
    def name(self) -> str:
        return "problem_verify_tests"

    @property
    def description(self) -> str:
        return """验证生成的测试数据质量。

        自动执行以下检查：
        1. file_count: 每个 .in 有对应的 .ans，文件名连续
        2. answer_consistency: 用 sol 重新运行 .in，对比输出与 .ans
        3. validator: 用 val 检查每个 .in 是否满足约束（如有 val.exe）
        4. no_empty: 没有空文件
        5. limit_ratio: 最终测试中 extreme/tle（type=3/4）不少于一半（需存在 manifest）

        前置条件：
        1. 已运行 problem_generate_tests 生成测试数据
        2. 已运行 solution_build 构建 sol

        建议下一步：
        - 如果验证通过：运行 problem_pack_polygon 打包
        - 如果验证失败：根据失败信息修复问题
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "problem_dir": {
                    "type": "string",
                    "description": "题目目录路径",
                },
                "tests_dir": {
                    "type": "string",
                    "description": "测试数据目录路径，默认为 problem_dir/tests",
                },
                "verify_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "file_count",
                            "answer_consistency",
                            "validator",
                            "no_empty",
                            "limit_ratio",
                        ],
                    },
                    "description": "要执行的验证类型，默认全部执行",
                },
                "sol_name": {
                    "type": "string",
                    "description": "标准解法文件名（不含扩展名），默认 'sol'",
                },
                "enable_limit_ratio": {
                    "type": "boolean",
                    "description": "是否启用 extreme/tle 占比检查（默认开启；设为 false 可关闭）",
                    "default": True,
                },
                "timeout": {
                    "type": "integer",
                    "description": "单次执行超时（秒）",
                    "default": 60,
                },
            },
            "required": ["problem_dir"],
        }

    async def execute(
        self,
        problem_dir: str,
        tests_dir: str | None = None,
        verify_types: list[str] | None = None,
        sol_name: str | None = None,
        enable_limit_ratio: bool = True,
        timeout: int = 60,
    ) -> ToolResult:
        """执行测试数据验证。"""
        effective_sol_name = sol_name or "sol"

        # 解析测试目录
        if tests_dir:
            if not os.path.isabs(tests_dir):
                tests_dir = os.path.join(problem_dir, tests_dir)
        else:
            tests_dir = os.path.join(problem_dir, "tests")

        if not os.path.exists(tests_dir):
            return ToolResult.fail(f"Tests directory not found: {tests_dir}")

        # 默认执行所有验证
        if not verify_types:
            verify_types = ["file_count", "answer_consistency", "validator", "no_empty"]

        if enable_limit_ratio:
            if "limit_ratio" not in verify_types:
                verify_types.append("limit_ratio")
        else:
            verify_types = [v for v in verify_types if v != "limit_ratio"]

        results = {}
        all_passed = True

        # 1. 文件完整性检查
        if "file_count" in verify_types:
            result = self._check_file_count(tests_dir)
            results["file_count"] = result
            if not result["passed"]:
                all_passed = False

        # 2. 空文件检查
        if "no_empty" in verify_types:
            result = self._check_no_empty(tests_dir)
            results["no_empty"] = result
            if not result["passed"]:
                all_passed = False

        # 3. 答案一致性检查
        if "answer_consistency" in verify_types:
            result = await self._check_answer_consistency(
                problem_dir,
                tests_dir,
                effective_sol_name,
                timeout,
            )
            results["answer_consistency"] = result
            if not result["passed"]:
                all_passed = False

        # 4. Validator 检查
        if "validator" in verify_types:
            result = await self._check_validator(problem_dir, tests_dir, timeout)
            results["validator"] = result
            if not result["passed"]:
                all_passed = False

        # 5. 极限数据占比检查
        if "limit_ratio" in verify_types:
            result = self._check_limit_ratio(tests_dir)
            results["limit_ratio"] = result
            if not result["passed"]:
                all_passed = False

        # 汇总
        total_checks = len(results)
        passed_checks = sum(1 for r in results.values() if r["passed"])

        if all_passed:
            return ToolResult.ok(
                passed=True,
                results=results,
                total_checks=total_checks,
                passed_checks=passed_checks,
                tests_dir=tests_dir,
                sol_name=effective_sol_name,
                limit_ratio_enabled=enable_limit_ratio,
                message=f"All {total_checks} verification checks passed",
            )
        else:
            return ToolResult.fail(
                f"{passed_checks}/{total_checks} checks passed",
                passed=False,
                results=results,
                total_checks=total_checks,
                passed_checks=passed_checks,
                tests_dir=tests_dir,
                sol_name=effective_sol_name,
                limit_ratio_enabled=enable_limit_ratio,
            )

    def _check_file_count(self, tests_dir: str) -> dict:
        """检查文件完整性：每个 .in 有对应的 .ans。"""
        tests_path = Path(tests_dir)
        in_files = sorted(p.name for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in")
        ans_files = sorted(p.name for p in tests_path.iterdir() if p.is_file() and p.suffix == ".ans")
        ans_file_set = set(ans_files)
        in_file_set = set(in_files)

        missing_ans = []
        for in_file in in_files:
            ans_file = Path(in_file).with_suffix(".ans").name
            if ans_file not in ans_file_set:
                missing_ans.append(in_file)

        orphan_ans = []
        for ans_file in ans_files:
            in_file = Path(ans_file).with_suffix(".in").name
            if in_file not in in_file_set:
                orphan_ans.append(ans_file)

        non_numeric = [f for f in in_files if not Path(f).stem.isdigit()]
        numeric_indices = sorted(int(Path(f).stem) for f in in_files if Path(f).stem.isdigit())
        numeric_index_set = set(numeric_indices)
        expected_indices = list(range(1, max(numeric_indices) + 1)) if numeric_indices else []
        missing_indices = [
            idx for idx in expected_indices if idx not in numeric_index_set
        ]
        duplicate_indices = sorted(
            idx for idx in numeric_index_set if numeric_indices.count(idx) > 1
        )

        passed = (
            not missing_ans
            and not orphan_ans
            and not non_numeric
            and not missing_indices
            and not duplicate_indices
        )
        return {
            "passed": passed,
            "total": len(in_files),
            "missing_ans": missing_ans,
            "orphan_ans": orphan_ans,
            "missing_indices": missing_indices,
            "duplicate_indices": duplicate_indices,
            "non_numeric": non_numeric,
        }

    def _check_no_empty(self, tests_dir: str) -> dict:
        """检查没有空文件。"""
        empty_files = []
        for f in os.listdir(tests_dir):
            filepath = os.path.join(tests_dir, f)
            if os.path.isfile(filepath) and os.path.getsize(filepath) == 0:
                empty_files.append(f)

        return {
            "passed": len(empty_files) == 0,
            "total": len(os.listdir(tests_dir)),
            "empty_files": empty_files,
        }

    async def _check_answer_consistency(
        self, problem_dir: str, tests_dir: str, sol_name: str, timeout: int
    ) -> dict:
        """用 sol 重新运行 .in，对比输出与 .ans。"""
        exe_ext = get_exe_extension()
        sol_exe = os.path.join(problem_dir, "solutions", f"{sol_name}{exe_ext}")
        if not os.path.exists(sol_exe):
            sol_exe = os.path.join(problem_dir, f"{sol_name}{exe_ext}")

        if not os.path.exists(sol_exe):
            return {
                "passed": False,
                "total": 0,
                "mismatches": [],
                "error": f"{sol_name}{exe_ext} not found, run solution_build first",
            }

        in_files = sorted(
            f for f in os.listdir(tests_dir) if f.endswith(".in")
        )

        mismatches = []
        timed_out = []
        errors = []

        for in_file in in_files:
            in_path = os.path.join(tests_dir, in_file)
            ans_file = Path(in_file).with_suffix(".ans").name
            ans_path = os.path.join(tests_dir, ans_file)

            if not os.path.exists(ans_path):
                continue

            with open(in_path, encoding="utf-8") as f:
                input_data = f.read()

            with open(ans_path, encoding="utf-8") as f:
                expected = f.read()

            result = await run_binary(sol_exe, input_data, timeout=timeout)

            if result.timed_out:
                timed_out.append(in_file)
                continue

            if not result.success:
                errors.append({"file": in_file, "stderr": result.stderr})
                continue

            if result.stdout.strip() != expected.strip():
                mismatches.append({
                    "file": in_file,
                    "expected": expected[:200],
                    "actual": result.stdout[:200],
                })

        passed = not mismatches and not timed_out and not errors
        return {
            "passed": passed,
            "total": len(in_files),
            "mismatches": mismatches,
            "timed_out": timed_out,
            "errors": errors,
        }

    async def _check_validator(
        self, problem_dir: str, tests_dir: str, timeout: int
    ) -> dict:
        """用 val 检查每个 .in 是否满足约束。"""
        exe_ext = get_exe_extension()
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")

        if not os.path.exists(val_exe):
            return {
                "passed": True,
                "total": 0,
                "skipped": True,
                "message": "val.exe not found, validator check skipped",
            }

        in_files = sorted(
            f for f in os.listdir(tests_dir) if f.endswith(".in")
        )

        invalid = []
        for in_file in in_files:
            in_path = os.path.join(tests_dir, in_file)

            with open(in_path, encoding="utf-8") as f:
                input_data = f.read()

            result = await run_binary(val_exe, input_data, timeout=timeout)

            if result.return_code != 0:
                invalid.append({
                    "file": in_file,
                    "stderr": result.stderr[:200] if result.stderr else "",
                })

        return {
            "passed": len(invalid) == 0,
            "total": len(in_files),
            "invalid": invalid,
        }

    def _check_limit_ratio(self, tests_dir: str) -> dict:
        """检查最终测试中 type=3/4 是否不少于一半。"""
        manifest_path = os.path.join(tests_dir, _TEST_MANIFEST_FILENAME)
        if not os.path.exists(manifest_path):
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": f"manifest not found: {manifest_path}",
            }

        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": f"failed to read manifest: {e}",
            }

        tests = manifest.get("tests", [])
        if not isinstance(tests, list):
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "invalid manifest format: tests must be a list",
            }

        in_files = sorted(f for f in os.listdir(tests_dir) if f.endswith(".in"))
        in_file_set = set(in_files)
        type_by_in_file: dict[str, str] = {}
        for item in tests:
            if not isinstance(item, dict):
                continue
            in_file = item.get("in_file")
            type_param = item.get("type_param")
            if isinstance(in_file, str) and isinstance(type_param, str):
                type_by_in_file[in_file] = type_param

        missing_in_manifest = sorted(f for f in in_files if f not in type_by_in_file)
        if missing_in_manifest:
            return {
                "passed": False,
                "total": len(in_files),
                "limit_case_count": 0,
                "limit_case_minimum_required": (len(in_files) + 1) // 2 if in_files else 0,
                "limit_case_ratio": 0.0,
                "missing_in_manifest": missing_in_manifest,
                "error": "manifest does not cover all .in files",
            }

        total = len(in_files)
        if total == 0:
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "no .in files found",
            }

        limit_case_count = sum(
            1 for in_file in in_file_set if type_by_in_file[in_file] in _LIMIT_STRATEGY_TYPES
        )
        minimum_required = (total + 1) // 2
        ratio = limit_case_count / total

        return {
            "passed": limit_case_count >= minimum_required,
            "total": total,
            "limit_case_count": limit_case_count,
            "limit_case_minimum_required": minimum_required,
            "limit_case_ratio": ratio,
            "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
        }
