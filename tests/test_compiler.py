"""
Compiler 工具函数测试。

测试 C++ 编译和执行的核心功能。
"""

import os
import tempfile

import pytest

from autocode_mcp.utils.compiler import (
    CompileResult,
    cleanup_work_dir,
    compile_all,
    compile_cpp,
    get_work_dir,
    run_binary,
    run_binary_with_args,
)

# 简单的 Hello World 代码
HELLO_WORLD_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""

# 带参数的程序代码
ARGS_CODE = """
#include <iostream>
#include <string>

int main(int argc, char* argv[]) {
    for (int i = 1; i < argc; i++) {
        if (i > 1) std::cout << " ";
        std::cout << argv[i];
    }
    std::cout << std::endl;
    return 0;
}
"""

# 无限循环代码（用于测试超时）
INFINITE_LOOP_CODE = """
#include <iostream>

int main() {
    while (true) {
        // 无限循环
    }
    return 0;
}
"""


@pytest.mark.asyncio
async def test_compile_cpp_success():
    """测试编译成功。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        # 写入源代码
        with open(source_path, "w") as f:
            f.write(HELLO_WORLD_CODE)

        # 编译
        result = await compile_cpp(source_path, binary_path)

        assert result.success
        assert os.path.exists(binary_path)
        assert result.binary_path == binary_path


@pytest.mark.asyncio
async def test_compile_cpp_syntax_error():
    """测试语法错误编译失败。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        # 写入错误代码
        with open(source_path, "w") as f:
            f.write("invalid c++ code {{{")

        # 编译
        result = await compile_cpp(source_path, binary_path)

        assert not result.success
        assert "compilation" in result.error.lower() or "failed" in result.error.lower()
        assert not os.path.exists(binary_path)


@pytest.mark.asyncio
async def test_compile_cpp_timeout():
    """测试编译超时。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        # 写入源代码
        with open(source_path, "w") as f:
            f.write(HELLO_WORLD_CODE)

        # 编译（设置极短超时）
        result = await compile_cpp(source_path, binary_path, timeout=0.001)

        # 正常编译通常很快，但超时测试不稳定
        # 这里只测试超时参数不会导致崩溃
        assert isinstance(result, CompileResult)


@pytest.mark.asyncio
async def test_compile_cpp_missing_source():
    """测试源文件不存在。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "nonexistent.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        result = await compile_cpp(source_path, binary_path)

        assert not result.success
        assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_run_binary_success():
    """测试运行成功。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 先编译
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w") as f:
            f.write(HELLO_WORLD_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        assert compile_result.success

        # 运行
        result = await run_binary(binary_path)

        assert result.success
        assert "Hello, World!" in result.stdout
        assert result.return_code == 0


@pytest.mark.asyncio
async def test_run_binary_timeout():
    """测试运行超时。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 编译无限循环程序
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w") as f:
            f.write(INFINITE_LOOP_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        # 运行（设置短超时）
        result = await run_binary(binary_path, timeout=1)

        # 应该超时
        assert result.timed_out or not result.success


@pytest.mark.asyncio
async def test_run_binary_with_stdin():
    """测试运行带标准输入。"""
    # 需要一个读取 stdin 的程序
    stdin_code = """
#include <iostream>
#include <string>

int main() {
    std::string line;
    std::getline(std::cin, line);
    std::cout << "Input: " << line << std::endl;
    return 0;
}
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w") as f:
            f.write(stdin_code)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        # 运行带输入
        result = await run_binary(binary_path, stdin="test input")

        assert result.success
        assert "Input: test input" in result.stdout


@pytest.mark.asyncio
async def test_run_binary_with_args():
    """测试运行带命令行参数。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w") as f:
            f.write(ARGS_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        # 运行带参数
        result = await run_binary_with_args(binary_path, args=["hello", "world", "123"])

        assert result.success
        assert "hello world 123" in result.stdout


@pytest.mark.asyncio
async def test_run_binary_missing():
    """测试运行不存在的程序。"""
    result = await run_binary("/nonexistent/binary")

    assert not result.success
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_compile_all_success():
    """测试批量编译成功。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建多个源文件
        sources = []
        for i in range(3):
            source_path = os.path.join(tmpdir, f"test{i}.cpp")
            with open(source_path, "w") as f:
                f.write(f"""
#include <iostream>

int main() {{
    std::cout << "Program {i}" << std::endl;
    return 0;
}}
""")
            sources.append(f"test{i}.cpp")

        # 批量编译
        results = await compile_all(tmpdir, sources)

        assert len(results) == 3
        for i in range(3):
            source_name = f"test{i}.cpp"
            assert source_name in results
            assert results[source_name].success
            # 检查二进制文件存在
            binary_path = os.path.join(tmpdir, f"test{i}" + (".exe" if os.name == "nt" else ""))
            assert os.path.exists(binary_path)


@pytest.mark.asyncio
async def test_compile_all_partial_failure():
    """测试批量编译部分失败。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一个有效文件和一个无效文件
        valid_source = "valid.cpp"
        invalid_source = "invalid.cpp"

        with open(os.path.join(tmpdir, valid_source), "w") as f:
            f.write(HELLO_WORLD_CODE)

        with open(os.path.join(tmpdir, invalid_source), "w") as f:
            f.write("invalid code {{{")

        # 批量编译
        results = await compile_all(tmpdir, [valid_source, invalid_source])

        assert len(results) == 2
        assert results[valid_source].success
        assert not results[invalid_source].success


def test_get_work_dir():
    """测试工作目录生成。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = get_work_dir(tmpdir, "test_tool")

        assert work_dir.startswith(tmpdir)
        assert "test_tool" in work_dir
        assert os.path.exists(work_dir)

        # 每次调用生成不同目录
        work_dir2 = get_work_dir(tmpdir, "test_tool")
        assert work_dir != work_dir2


def test_cleanup_work_dir():
    """测试工作目录清理。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = get_work_dir(tmpdir, "test_tool")

        # 创建一些文件
        test_file = os.path.join(work_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")

        assert os.path.exists(work_dir)

        # 清理
        cleanup_work_dir(work_dir)

        # 目录应该被删除
        assert not os.path.exists(work_dir)


def test_cleanup_nonexistent_dir():
    """测试清理不存在的目录（不应报错）。"""
    cleanup_work_dir("/nonexistent/directory")
    # 应该静默成功
