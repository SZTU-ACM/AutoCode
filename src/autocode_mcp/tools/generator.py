"""
Generator 工具组 - 数据生成器。

基于论文 Algorithm 2: BUILDGENERATORSUITE 实现。
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re

from ..utils.compiler import run_binary, run_binary_with_args
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult, input_schema_from_model
from .mixins import BuildToolMixin, resolve_source
from .schemas import GeneratorBuildInput, GeneratorRunInput


class GeneratorBuildTool(Tool, BuildToolMixin):
    """构建数据生成器。"""

    @property
    def name(self) -> str:
        return "generator_build"

    @property
    def description(self) -> str:
        return """构建数据生成器。

        保存并编译 gen.cpp。

        规范要求（testlib.h）：
        - 源码文件需包含 #include "testlib.h"
        - 入口函数首行必须调用 registerGen(argc, argv, 1);
        - 命令行传参结构：gen <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]
        - 类型参数语义：1=tiny, 2=random, 3=extreme, 4=tle

        前置条件：
        1. 已运行 problem_create 创建题目目录

        建议下一步：
        - 运行 validator_build 构建校验器
        - 运行 stress_test_run 进行对拍测试
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(GeneratorBuildInput)

    async def execute(
        self,
        problem_dir: str,
        code: str | None = None,
        source_path: str | None = None,
        compiler: str = "g++",
        enable_semantic_check: bool = True,
        strict_semantic_check: bool = False,
    ) -> ToolResult:
        """执行 Generator 构建。"""
        resolved, err = resolve_source(
            problem_dir,
            code,
            source_path,
            default_source_path=os.path.join("files", "gen.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None

        os.makedirs(problem_dir, exist_ok=True)
        files_dir = os.path.join(problem_dir, "files")
        os.makedirs(files_dir, exist_ok=True)

        canonical_path = os.path.join(files_dir, "gen.cpp")
        try:
            with open(canonical_path, "w", encoding="utf-8") as f:
                f.write(resolved.code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        exe_ext = get_exe_extension()
        binary_path = os.path.join(files_dir, f"gen{exe_ext}")

        compile_source = resolved.original_source_path or canonical_path
        include_dirs = [resolved.include_dir] if resolved.include_dir else None
        compile_result = await self.build(compile_source, binary_path, compiler=compiler, include_dirs=include_dirs)

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=compile_source,
                canonical_path=canonical_path,
                compile_log=compile_result.stderr,
            )

        binary_size = os.path.getsize(binary_path) if os.path.exists(binary_path) else 0

        semantic_check = self._check_type34_semantics(resolved.code) if enable_semantic_check else {"enabled": False}
        if (
            enable_semantic_check
            and strict_semantic_check
            and not semantic_check.get("passed", True)
        ):
            return ToolResult.fail(
                "Generator semantic check failed: type=3/type=4 lack substantial difference",
                semantic_check=semantic_check,
            )

        return ToolResult.ok(
            source_path=compile_source,
            canonical_path=canonical_path,
            binary_path=binary_path,
            binary_size=binary_size,
            compile_log=compile_result.stderr,
            semantic_check=semantic_check,
            message="Generator built successfully",
        )

    def _check_type34_semantics(self, code: str) -> dict:
        stripped = self._strip_cpp_comments_and_strings(code)
        type3_blocks = self._extract_type_branch_snippets(stripped, 3)
        type4_blocks = self._extract_type_branch_snippets(stripped, 4)
        has_type3 = bool(type3_blocks)
        has_type4 = bool(type4_blocks)
        if not has_type3 or not has_type4:
            return {
                "enabled": True,
                "passed": True,
                "advisory": True,
                "reason": "semantic check could not reliably detect both type=3/type=4 branches",
                "hint": "请人工确认 type=3/type=4 分支存在且有实质差异",
            }

        joined3 = " ".join(type3_blocks)
        joined4 = " ".join(type4_blocks)
        norm3 = re.sub(r"\s+", "", joined3)
        norm4 = re.sub(r"\s+", "", joined4)
        tokens3 = re.findall(r"[a-zA-Z_]\w*|\d+|[^\s\w]", joined3)
        tokens4 = re.findall(r"[a-zA-Z_]\w*|\d+|[^\s\w]", joined4)
        body_similarity = (
            difflib.SequenceMatcher(None, tokens3, tokens4).ratio()
            if (tokens3 and tokens4)
            else 0.0
        )
        type3_signals = self._extract_branch_signals(joined3)
        type4_signals = self._extract_branch_signals(joined4)
        shared_signals = type3_signals.intersection(type4_signals)
        signal_overlap = (
            len(shared_signals) / max(len(type3_signals.union(type4_signals)), 1)
            if (type3_signals or type4_signals)
            else 1.0
        )
        similar_core = (bool(norm3) and norm3 == norm4) or (
            body_similarity >= 0.96 and signal_overlap >= 0.95
        )
        overlap_advisory = signal_overlap > 0.9 and not similar_core
        return {
            "enabled": True,
            "passed": not similar_core,
            "reason": "" if not similar_core else "type=3/type=4 branch snippets are too similar",
            "hint": "为 type=4 增加针对性卡法，而不仅是 n_max/t_max 取最大值",
            "signal_overlap": round(signal_overlap, 3),
            "signal_overlap_advisory": overlap_advisory,
            "body_similarity": round(body_similarity, 3),
            "type3_signals": sorted(type3_signals)[:20],
            "type4_signals": sorted(type4_signals)[:20],
        }

    @staticmethod
    def _strip_cpp_comments_and_strings(code: str) -> str:
        """移除 C++ 注释并清空字符串字面量内容，保留代码结构以供花括号配对。"""
        out: list[str] = []
        i = 0
        n = len(code)
        while i < n:
            ch = code[i]
            nxt = code[i + 1] if i + 1 < n else ""
            if ch == "/" and nxt == "/":
                out.append("  ")
                i += 2
                while i < n and code[i] != "\n":
                    out.append(" ")
                    i += 1
            elif ch == "/" and nxt == "*":
                out.append("  ")
                i += 2
                while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                    out.append("\n" if code[i] == "\n" else " ")
                    i += 1
                if i + 1 < n:
                    out.append("  ")
                    i += 2
            elif ch == '"' or ch == "'":
                quote = ch
                out.append(quote)
                i += 1
                while i < n:
                    if code[i] == "\\":
                        i += 2
                        continue
                    if code[i] == quote:
                        i += 1
                        break
                    i += 1
                out.append(quote)
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    @staticmethod
    def _find_matching_delimiter(code: str, open_idx: int, open_ch: str, close_ch: str) -> int:
        depth = 0
        n = len(code)
        for i in range(open_idx, n):
            if code[i] == open_ch:
                depth += 1
            elif code[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return i
        return n - 1

    def _extract_statement_or_block(self, code: str, start_idx: int) -> str:
        n = len(code)
        i = start_idx
        while i < n and code[i].isspace():
            i += 1
        if i >= n:
            return ""
        if code[i] == "{":
            end_idx = self._find_matching_delimiter(code, i, "{", "}")
            return code[i : end_idx + 1]
        # 单语句分支（无大括号），截断到顶层分号或块结尾。
        paren_depth = 0
        brace_depth = 0
        for j in range(i, n):
            ch = code[j]
            if ch == "(":
                paren_depth += 1
            elif ch == ")" and paren_depth > 0:
                paren_depth -= 1
            elif ch == "{":
                brace_depth += 1
            elif ch == "}":
                if brace_depth == 0:
                    return code[i:j]
                brace_depth -= 1
            elif ch == ";" and paren_depth == 0 and brace_depth == 0:
                return code[i : j + 1]
        return code[i:]

    def _extract_case_body(self, code: str, colon_after_idx: int) -> str:
        n = len(code)
        i = colon_after_idx
        while i < n and code[i].isspace():
            i += 1
        if i >= n:
            return ""
        # 显式 `case k: { ... }` 块
        if code[i] == "{":
            end_idx = self._find_matching_delimiter(code, i, "{", "}")
            return code[i : end_idx + 1]
        # fallthrough `case 3:\n case 4: body`: 若紧接着另一个 case/default，继续向后找主体以便识别共用实现。
        while True:
            fallthrough = re.match(r"(?:case\s+\d+|default)\s*:\s*", code[i:])
            if not fallthrough:
                break
            i += fallthrough.end()
            if i < n and code[i] == "{":
                end_idx = self._find_matching_delimiter(code, i, "{", "}")
                return code[i : end_idx + 1]
        brace_depth = 0
        j = i
        while j < n:
            ch = code[j]
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                if brace_depth == 0:
                    return code[i:j]
                brace_depth -= 1
            elif brace_depth == 0 and re.match(r"\b(?:case\s+\d+|default)\s*:", code[j:]):
                return code[i:j]
            j += 1
        return code[i:]

    def _extract_type_branch_snippets(self, code: str, type_value: int) -> list[str]:
        """用平衡括号与花括号配对提取指定 `type` 分支的函数体/语句。"""
        snippets: list[str] = []
        if_pattern = rf"\b(?:type\s*==\s*{type_value}|{type_value}\s*==\s*type)\b"
        for match in re.finditer(if_pattern, code):
            # 向上追溯包含该条件的 if(...) 头部并跳过整段条件括号。
            search_window_start = max(0, match.start() - 160)
            if_matches = list(re.finditer(r"\bif\s*\(", code[search_window_start : match.start() + 1]))
            body = ""
            for if_m in reversed(if_matches):
                open_paren = search_window_start + if_m.end() - 1
                close_paren = self._find_matching_delimiter(code, open_paren, "(", ")")
                if open_paren <= match.start() <= close_paren:
                    body = self._extract_statement_or_block(code, close_paren + 1)
                    break
            if not body:
                # 兜底：若形如三目或特殊写法，提取条件表达式之后紧跟的一条块/语句（跳过当前圆括号）
                close_idx = code.find(")", match.end())
                start = close_idx + 1 if close_idx != -1 and close_idx - match.end() < 40 else match.end()
                body = self._extract_statement_or_block(code, start)
            if body:
                snippets.append(body)

        case_pattern = rf"\bcase\s+{type_value}\s*:"
        for match in re.finditer(case_pattern, code):
            body = self._extract_case_body(code, match.end())
            if body:
                snippets.append(body)

        return snippets

    def _extract_branch_signals(self, snippet: str) -> set[str]:
        """提取分支内结构信号，用于判断 type=3/type=4 是否只有参数放大差异。"""
        tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", snippet))
        stop_words = {
            "if", "else", "case", "switch", "for", "while", "return", "int", "long", "double",
            "seed", "type", "n_min", "n_max", "t_min", "t_max",
        }
        return {t for t in tokens if t not in stop_words and len(t) > 2}


class GeneratorRunTool(Tool):
    """运行多策略数据生成器。"""

    @property
    def name(self) -> str:
        return "generator_run"

    @property
    def description(self) -> str:
        return """运行多策略数据生成器。

        基于论文 Algorithm 2 实现三种策略:
        - tiny: 小数据穷举 (G1)
        - random: 随机数据 (G2)
        - extreme: 极端数据 (溢出、精度、hash碰撞)
        - tle: TLE 诱导数据 (G3)

        自动通过 Validator 过滤无效输入。
        支持去重、平衡、采样。

        前置条件：
        1. 已运行 generator_build 构建生成器

        建议下一步：
        - 运行 stress_test_run 验证解法正确性
        - 运行 problem_generate_tests 生成最终测试数据
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(GeneratorRunInput)

    async def execute(
        self,
        problem_dir: str,
        strategies: list[str],
        test_count: int = 20,
        validator_path: str | None = None,
        seed_start: int = 1,
        n_min: int = 1,
        n_max: int = 100000,
        t_min: int = 1,
        t_max: int = 1,
        extra_args: list[str] | None = None,
    ) -> ToolResult:
        """执行数据生成。"""
        exe_ext = get_exe_extension()
        extra_args = extra_args or []

        # 检查 generator - 优先查找 files/ 子目录
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        if not os.path.exists(gen_exe):
            gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")

        # 检查 validator
        val_exe = validator_path
        if val_exe and not os.path.exists(val_exe):
            val_exe = None

        generated_inputs = []
        signatures = set()  # 用于去重
        generator_failures: list[dict[str, object]] = []

        # 策略映射到 type 参数
        strategy_type_map = {
            "tiny": "1",
            "random": "2",
            "extreme": "3",
            "tle": "4",
        }

        seed = seed_start
        attempts = 0
        max_attempts = test_count * 10  # 最多尝试 10 倍

        while len(generated_inputs) < test_count and attempts < max_attempts:
            attempts += 1

            # 选择策略
            strategy = strategies[(attempts - 1) % len(strategies)]
            type_param = strategy_type_map.get(strategy, "2")

            # 运行 generator
            # gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]
            cmd_args = [str(seed), type_param, str(n_min), str(n_max), str(t_min), str(t_max)] + extra_args

            gen_result = await run_binary_with_args(
                gen_exe,
                cmd_args,
                timeout=10,
            )

            if not gen_result.success:
                generator_failures.append(
                    {
                        "seed": seed,
                        "strategy": strategy,
                        "return_code": gen_result.return_code,
                        "stderr": (gen_result.stderr or "")[:200],
                    }
                )
                seed += 1
                continue

            input_data = gen_result.stdout
            if not input_data or not input_data.strip():
                generator_failures.append(
                    {
                        "seed": seed,
                        "strategy": strategy,
                        "return_code": gen_result.return_code,
                        "stderr": (gen_result.stderr or "")[:200],
                        "reason": "empty_stdout",
                    }
                )
                seed += 1
                continue

            # 计算 signature 用于去重
            sig = hashlib.md5(input_data.encode()).hexdigest()
            if sig in signatures:
                continue
            signatures.add(sig)

            # 使用 validator 过滤
            if val_exe:
                val_result = await run_binary(val_exe, input_data, timeout=5)
                if val_result.return_code != 0:
                    continue

            generated_inputs.append(
                {
                    "input": input_data,
                    "strategy": strategy,
                    "seed": seed,
                }
            )
            seed += 1

        return ToolResult.ok(
            generated_count=len(generated_inputs),
            test_count=test_count,
            inputs=generated_inputs[:test_count],
            strategies_used=strategies,
            generator_failures=generator_failures[-20:],
            message=f"Generated {len(generated_inputs)} test inputs",
        )
