"""
Problem 工具组 - 题目管理。
"""
import os
import shutil

from ..utils.compiler import run_binary
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult


class ProblemCreateTool(Tool):
    """创建题目目录结构。"""

    @property
    def name(self) -> str:
        return "problem_create"

    @property
    def description(self) -> str:
        return """创建新题目的目录结构。

        创建标准的竞赛编程题目目录：
        - files/: testlib.h, gen.cpp, val.cpp
        - solutions/: sol.cpp, brute.cpp
        - statements/: README.md
        - tests/: 测试数据

        同时复制 testlib.h 模板文件。
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
                "problem_name": {
                    "type": "string",
                    "description": "题目名称",
                },
            },
            "required": ["problem_dir", "problem_name"],
        }

    async def execute(
        self,
        problem_dir: str,
        problem_name: str,
    ) -> ToolResult:
        """执行题目目录创建。"""
        # 创建目录结构
        directories = [
            problem_dir,
            os.path.join(problem_dir, "files"),
            os.path.join(problem_dir, "solutions"),
            os.path.join(problem_dir, "statements"),
            os.path.join(problem_dir, "tests"),
        ]

        created_dirs = []
        for dir_path in directories:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                created_dirs.append(dir_path)

        # 复制 testlib.h
        # 查找模板目录
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_testlib = os.path.join(package_dir, "..", "..", "templates", "testlib.h")

        if os.path.exists(template_testlib):
            dest_testlib = os.path.join(problem_dir, "files", "testlib.h")
            shutil.copy2(template_testlib, dest_testlib)
        else:
            # 如果模板不存在，创建一个占位符
            dest_testlib = os.path.join(problem_dir, "files", "testlib.h")
            with open(dest_testlib, "w") as f:
                f.write("// testlib.h - Please download from https://github.com/MikeMirzayanov/testlib\n")

        # 创建基础 README.md
        readme_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(f"# {problem_name}\n\n题目描述待补充...\n")

        return ToolResult.ok(
            problem_dir=problem_dir,
            problem_name=problem_name,
            created_directories=created_dirs,
            message=f"Created problem directory: {problem_dir}",
        )


class ProblemGenerateTestsTool(Tool):
    """生成最终测试数据。"""

    @property
    def name(self) -> str:
        return "problem_generate_tests"

    @property
    def description(self) -> str:
        return """生成最终测试数据集。

        基于论文 Algorithm 2 的后处理步骤：
        - 使用 gen.cpp 生成测试数据
        - 使用 sol.cpp 生成答案
        - 支持去重、平衡、采样

        生成 01.in ~ N.in 及对应的 .ans 文件。
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
                "test_count": {
                    "type": "integer",
                    "description": "测试数据组数",
                    "default": 20,
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
        test_count: int = 20,
        timeout: int = 60,
    ) -> ToolResult:
        """执行测试数据生成。"""
        exe_ext = get_exe_extension()

        # 检查必要文件
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        sol_exe = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
        tests_dir = os.path.join(problem_dir, "tests")

        # 如果 files 目录下没有，检查根目录
        if not os.path.exists(gen_exe):
            gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")
        if not os.path.exists(sol_exe):
            sol_exe = os.path.join(problem_dir, f"sol{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")
        if not os.path.exists(sol_exe):
            return ToolResult.fail("sol not found. Run solution_build first.")

        # 创建/清空 tests 目录
        if os.path.exists(tests_dir):
            shutil.rmtree(tests_dir)
        os.makedirs(tests_dir)

        generated_tests = []
        errors = []

        # 定义测试配置
        # (seed_offset, type, n_min, n_max, t_min, t_max)
        test_configs = []

        # 小数据
        test_configs.extend([("1", "1", "1", "10", "1", "3")] * 3)

        # 随机数据
        test_configs.extend([
            ("2", "2", "10", "100", "1", "3"),
            ("2", "2", "100", "1000", "1", "3"),
            ("2", "2", "1000", "5000", "1", "3"),
            ("2", "2", "5000", "10000", "1", "3"),
        ])

        # 大数据
        test_configs.extend([
            ("3", "3", "100000", "200000", "1", "1"),
            ("3", "3", "150000", "200000", "1", "1"),
        ])

        # 边界数据
        test_configs.extend([
            ("4", "4", "10", "50", "1", "3"),
        ])

        for i, _config in enumerate(test_configs[:test_count], 1):
            test_file = os.path.join(tests_dir, f"{i:02d}.in")
            ans_file = os.path.join(tests_dir, f"{i:02d}.ans")

            try:
                # 生成输入
                gen_result = await run_binary(gen_exe, "", timeout=timeout)
                if not gen_result.success:
                    errors.append((i, f"Generator failed: {gen_result.stderr}"))
                    continue

                with open(test_file, "w") as f:
                    f.write(gen_result.stdout)

                # 生成答案
                sol_result = await run_binary(sol_exe, gen_result.stdout, timeout=timeout)
                if not sol_result.success:
                    errors.append((i, f"sol failed: {sol_result.stderr}"))
                    continue

                with open(ans_file, "w") as f:
                    f.write(sol_result.stdout)

                generated_tests.append(i)

            except Exception as e:
                errors.append((i, str(e)))

        if len(generated_tests) == test_count:
            return ToolResult.ok(
                tests_dir=tests_dir,
                generated_tests=generated_tests,
                message=f"Generated {len(generated_tests)} test cases",
            )
        else:
            return ToolResult.fail(
                f"Partial generation: {len(generated_tests)}/{test_count}",
                generated_tests=generated_tests,
                errors=errors,
            )


class ProblemPackPolygonTool(Tool):
    """打包为 Polygon 格式。"""

    @property
    def name(self) -> str:
        return "problem_pack_polygon"

    @property
    def description(self) -> str:
        return """将题目打包为 Polygon 格式。

        整理文件到 Polygon 标准目录结构：
        - files/: testlib.h, gen.cpp, val.cpp
        - solutions/: sol.cpp, brute.cpp
        - statements/: README.md
        - tests/: 测试数据
        - problem.xml: 配置文件
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
                "time_limit": {
                    "type": "integer",
                    "description": "时间限制（毫秒）",
                    "default": 1000,
                },
                "memory_limit": {
                    "type": "integer",
                    "description": "内存限制（字节）",
                    "default": 268435456,
                },
            },
            "required": ["problem_dir"],
        }

    async def execute(
        self,
        problem_dir: str,
        time_limit: int = 1000,
        memory_limit: int = 268435456,
    ) -> ToolResult:
        """执行 Polygon 打包。"""
        if not os.path.exists(problem_dir):
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        results = {
            "files_copied": [],
            "files_removed": [],
            "directories_created": [],
        }

        # 1. 创建目录
        directories = ["files", "solutions", "statements", "scripts"]
        for dir_name in directories:
            dir_path = os.path.join(problem_dir, dir_name)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                results["directories_created"].append(dir_name)

        # 2. 移动文件到 files/
        files_dir = os.path.join(problem_dir, "files")
        for src in ["testlib.h", "gen.cpp", "val.cpp"]:
            src_path = os.path.join(problem_dir, src)
            if os.path.exists(src_path):
                dst_path = os.path.join(files_dir, src)
                shutil.copy2(src_path, dst_path)
                results["files_copied"].append(f"{src} -> files/{src}")

        # 3. 移动文件到 solutions/
        solutions_dir = os.path.join(problem_dir, "solutions")
        for src in ["sol.cpp", "brute.cpp"]:
            src_path = os.path.join(problem_dir, src)
            if os.path.exists(src_path):
                dst_path = os.path.join(solutions_dir, src)
                shutil.copy2(src_path, dst_path)
                results["files_copied"].append(f"{src} -> solutions/{src}")

        # 4. 移动 README.md
        statements_dir = os.path.join(problem_dir, "statements")
        readme_src = os.path.join(problem_dir, "README.md")
        if os.path.exists(readme_src):
            readme_dst = os.path.join(statements_dir, "README.md")
            shutil.copy2(readme_src, readme_dst)
            results["files_copied"].append("README.md -> statements/README.md")

        # 5. 创建 problem.xml
        problem_xml = os.path.join(problem_dir, "problem.xml")
        if not os.path.exists(problem_xml):
            problem_name = os.path.basename(problem_dir)
            xml_content = f'''<?xml version="1.0" encoding="utf-8" standalone="no"?>
<problem revision="1" short-name="{problem_name}">
    <names>
        <name language="chinese" value="{problem_name}"/>
    </names>
    <statements>
        <statement charset="UTF-8" language="chinese" mathjax="true" path="statements/README.md" type="application/x-tex"/>
    </statements>
    <judging>
        <testset name="tests">
            <time-limit>{time_limit}</time-limit>
            <memory-limit>{memory_limit}</memory-limit>
            <test-count>20</test-count>
            <input-path-pattern>tests/%02d.in</input-path-pattern>
            <answer-path-pattern>tests/%02d.ans</answer-path-pattern>
        </testset>
    </judging>
    <files>
        <resources>
            <file path="files/testlib.h"/>
        </resources>
        <executables>
            <executable>
                <source path="files/gen.cpp"/>
            </executable>
            <executable>
                <source path="files/val.cpp"/>
            </executable>
        </executables>
    </files>
    <assets>
        <solutions>
            <solution tag="main">
                <source path="solutions/sol.cpp"/>
            </solution>
            <solution tag="rejected">
                <source path="solutions/brute.cpp"/>
            </solution>
        </solutions>
    </assets>
</problem>
'''
            with open(problem_xml, "w", encoding="utf-8") as f:
                f.write(xml_content)
            results["files_copied"].append("problem.xml (created)")

        return ToolResult.ok(
            results=results,
            message="Packed to Polygon format",
        )
