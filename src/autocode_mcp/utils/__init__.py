"""
AutoCode MCP Utils 模块。
"""
from .compiler import (
    CompileResult,
    RunResult,
    cleanup_work_dir,
    compile_all,
    compile_cpp,
    get_work_dir,
    run_binary,
    run_binary_with_args,
)
from .platform import get_exe_extension, is_linux, is_macos, is_windows

__all__ = [
    "compile_cpp",
    "run_binary",
    "run_binary_with_args",
    "compile_all",
    "CompileResult",
    "RunResult",
    "get_work_dir",
    "cleanup_work_dir",
    "get_exe_extension",
    "is_windows",
    "is_linux",
    "is_macos",
]
