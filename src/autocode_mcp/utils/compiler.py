"""
编译和执行工具函数。

提供安全的 C++ 编译和执行能力，包含：
- 超时控制
- 资源限制
- 临时目录隔离
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .. import TEMPLATES_DIR
from .cache import CompileCache
from .platform import get_exe_extension
from .process import disable_posix_core_dumps, is_pid_alive, terminate_pid_tree

_psutil: Any
try:
    import psutil as _psutil
except ImportError:  # pragma: no cover
    _psutil = None

_resource: Any = None
if os.name != "nt":
    try:
        import resource as _resource
    except ImportError:  # pragma: no cover
        _resource = None

if TYPE_CHECKING:
    from .win_job import WinJobObject

# 模块级日志器
_logger = logging.getLogger(__name__)

_cache = CompileCache()


def _normalize_windows_stdin(stdin: str) -> str:
    """规范化 Windows 下传入程序的文本换行。

    先将 CRLF/CR 统一成 LF，再一次性转为 CRLF，避免重复转换导致的异常输入形态。
    """
    if not stdin:
        return stdin
    normalized = stdin.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n")


@dataclass
class CompileResult:
    """编译结果。"""

    success: bool
    binary_path: str | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    hint: str | None = None
    sanitizer_info: dict[str, Any] | None = None


_BITS_STDCPP_RE = re.compile(r"#\s*include\s*<\s*bits/stdc\+\+\.h\s*>")
_ALLOWED_COMPILER_BASENAME_RE = re.compile(
    r"\A(?:[A-Za-z0-9_.-]+-)?(?:g\+\+|clang\+\+|c\+\+|icpx)(?:-\d+(?:\.\d+)?)?(?:\.exe)?\Z",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:[\\/]")
_ALLOWED_EXTRA_FLAGS = frozenset(
    {
        "-fsanitize=address,undefined",
        "-fsanitize=address",
        "-fsanitize=undefined",
        "-fno-omit-frame-pointer",
        "-g",
        "-static-libasan",
        "-static-libubsan",
        "-D_GLIBCXX_ASSERTIONS",
    }
)
_COMPILER_INFO_CACHE: dict[str, dict[str, Any]] = {}
_SANITIZER_PROBE_CACHE: dict[str, dict[str, Any]] = {}

_SANITIZER_PROBE_CPP = r"""
#include <cstring>
#include <iostream>
int main(int argc, char** argv) {
    if (argc < 2) return 0;
    if (std::strcmp(argv[1], "ok") == 0) {
        volatile int n = 4;
        int* p = new int[n];
        p[0] = 1;
        int r = p[0] - 1;
        delete[] p;
        return r;
    }
    if (std::strcmp(argv[1], "asan") == 0) {
        volatile int n = 2;
        int* p = new int[n];
        p[0] = 1;
        p[1] = 2;
        volatile int idx = 5;
        p[idx] = 42;
        int r = p[0];
        delete[] p;
        return r == 1234567 ? 1 : 0;
    }
    if (std::strcmp(argv[1], "ubsan") == 0) {
        volatile int a = 2147483647;
        volatile int b = 1;
        volatile int c = a + b;
        return c == 0 ? 1 : 0;
    }
    return 0;
}
"""


def _detect_linux_preload() -> bool:
    """Detect /etc/ld.so.preload or LD_PRELOAD on Linux containers (which can break dynamic ASan)."""
    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("LD_PRELOAD", "").strip():
        return True
    try:
        if os.path.isfile("/etc/ld.so.preload"):
            with open("/etc/ld.so.preload", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        return True
    except OSError:
        pass
    return False


def _clone_sanitizer_info(info: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(info)
    cloned["flags"] = list(info.get("flags", []))
    cloned["env"] = dict(info.get("env", {}))
    if "attempts" in info:
        cloned["attempts"] = list(info["attempts"])
    return cloned


def probe_sanitizer_support(
    compiler: str = "g++",
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Probe working ASan/UBSan flags for ``compiler`` with a mandatory negative-control check.

    On Linux containers with ``/etc/ld.so.preload`` or ``LD_PRELOAD`` (or missing shared
    ``libubsan.so``), automatically falls back to ``-static-libasan -static-libubsan``.
    Verifies both clean execution AND that intentional heap-OOB / signed-overflow probes
    actually trigger non-zero exit + sanitizer diagnostics (never silently passing).
    """
    import tempfile

    try:
        validated = validate_compiler_spec((compiler or "g++").strip() or "g++")
    except ValueError as exc:
        return {
            "available": False,
            "compiler": str(compiler),
            "platform": sys.platform,
            "profile": None,
            "flags": [],
            "env": {},
            "asan_verified": False,
            "ubsan_verified": False,
            "negative_control_passed": False,
            "preload_detected": False,
            "reason": str(exc),
        }

    eff_compiler = _resolve_compiler(validated)
    preload_detected = _detect_linux_preload()
    cache_key = f"{sys.platform}|{eff_compiler}|preload={int(preload_detected)}"
    if use_cache and cache_key in _SANITIZER_PROBE_CACHE:
        return _clone_sanitizer_info(_SANITIZER_PROBE_CACHE[cache_key])

    base_asan_opts = "detect_leaks=0:abort_on_error=1:halt_on_error=1:disable_coredump=1"
    base_ubsan_opts = "print_stacktrace=1:halt_on_error=1"

    candidates: list[tuple[str, list[str], dict[str, str], bool, bool]]
    if sys.platform.startswith("linux"):
        dyn_both = (
            "dynamic",
            ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"],
            {"ASAN_OPTIONS": base_asan_opts, "UBSAN_OPTIONS": base_ubsan_opts},
            True,
            True,
        )
        static_both = (
            "static",
            [
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                "-g",
                "-static-libasan",
                "-static-libubsan",
            ],
            {"ASAN_OPTIONS": base_asan_opts, "UBSAN_OPTIONS": base_ubsan_opts},
            True,
            True,
        )
        dyn_no_order = (
            "dynamic_no_link_order",
            ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"],
            {
                "ASAN_OPTIONS": f"{base_asan_opts}:verify_asan_link_order=0",
                "UBSAN_OPTIONS": base_ubsan_opts,
            },
            True,
            True,
        )
        ubsan_static_assert = (
            "ubsan_static_assert",
            [
                "-fsanitize=undefined",
                "-fno-omit-frame-pointer",
                "-g",
                "-static-libubsan",
                "-D_GLIBCXX_ASSERTIONS",
            ],
            {"UBSAN_OPTIONS": base_ubsan_opts},
            False,
            True,
        )
        ubsan_static = (
            "ubsan_static",
            ["-fsanitize=undefined", "-fno-omit-frame-pointer", "-g", "-static-libubsan"],
            {"UBSAN_OPTIONS": base_ubsan_opts},
            False,
            True,
        )
        ubsan_dyn = (
            "ubsan_dynamic",
            ["-fsanitize=undefined", "-fno-omit-frame-pointer", "-g"],
            {"UBSAN_OPTIONS": base_ubsan_opts},
            False,
            True,
        )
        candidates = (
            [static_both, dyn_no_order, dyn_both, ubsan_static_assert, ubsan_static, ubsan_dyn]
            if preload_detected
            else [dyn_both, static_both, dyn_no_order, ubsan_static_assert, ubsan_static, ubsan_dyn]
        )
    elif sys.platform == "darwin":
        candidates = [
            (
                "dynamic",
                ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"],
                {"ASAN_OPTIONS": base_asan_opts, "UBSAN_OPTIONS": base_ubsan_opts},
                True,
                True,
            ),
            (
                "ubsan_dynamic",
                ["-fsanitize=undefined", "-fno-omit-frame-pointer", "-g"],
                {"UBSAN_OPTIONS": base_ubsan_opts},
                False,
                True,
            ),
        ]
    else:
        candidates = [
            (
                "dynamic",
                ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"],
                {"ASAN_OPTIONS": base_asan_opts, "UBSAN_OPTIONS": base_ubsan_opts},
                True,
                True,
            ),
        ]

    attempts: list[dict[str, Any]] = []
    exe_ext = get_exe_extension()
    with tempfile.TemporaryDirectory(prefix="autocode_san_probe_") as tmpdir:
        src_path = os.path.join(tmpdir, "probe.cpp")
        with open(src_path, "w", encoding="utf-8", newline="\n") as sf:
            sf.write(_SANITIZER_PROBE_CPP)

        for prof_name, flags, env_overrides, expect_asan, expect_ubsan in candidates:
            bin_path = os.path.join(tmpdir, f"probe_{prof_name}{exe_ext}")
            comp_cmd = [eff_compiler, "-std=c++17", "-O1", *flags, src_path, "-o", bin_path]
            try:
                comp = subprocess.run(
                    comp_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15.0,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                attempts.append({"profile": prof_name, "stage": "compile", "error": str(exc)})
                continue
            if comp.returncode != 0:
                attempts.append(
                    {
                        "profile": prof_name,
                        "stage": "compile",
                        "returncode": comp.returncode,
                        "stderr": (comp.stderr or "")[:300],
                    }
                )
                continue

            run_env = dict(os.environ)
            run_env.update(env_overrides)
            probe_run_kwargs: dict[str, Any] = {}
            if sys.platform != "win32":
                probe_run_kwargs["preexec_fn"] = disable_posix_core_dumps
            try:
                ok_run = subprocess.run(
                    [bin_path, "ok"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=run_env,
                    timeout=5.0,
                    check=False,
                    **probe_run_kwargs,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                attempts.append({"profile": prof_name, "stage": "ok_run", "error": str(exc)})
                continue
            if ok_run.returncode != 0:
                attempts.append(
                    {
                        "profile": prof_name,
                        "stage": "ok_run",
                        "returncode": ok_run.returncode,
                        "stderr": (ok_run.stderr or "")[:300],
                    }
                )
                continue

            asan_verified = False
            if expect_asan:
                # Prevent UBSan (e.g. object-size under GCC 12) from halting before ASan reports.
                asan_run_env = dict(run_env)
                asan_run_env["UBSAN_OPTIONS"] = "halt_on_error=0:print_stacktrace=0"
                try:
                    asan_run = subprocess.run(
                        [bin_path, "asan"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=asan_run_env,
                        timeout=5.0,
                        check=False,
                        **probe_run_kwargs,
                    )
                    asan_err = (asan_run.stderr or "") + (asan_run.stdout or "")
                    asan_verified = (
                        asan_run.returncode != 0 and "AddressSanitizer" in asan_err
                    )
                except (OSError, subprocess.SubprocessError):
                    asan_verified = False
                if not asan_verified:
                    attempts.append(
                        {"profile": prof_name, "stage": "negative_control_asan", "failed": True}
                    )
                    continue

            ubsan_verified = False
            if expect_ubsan:
                try:
                    ub_run = subprocess.run(
                        [bin_path, "ubsan"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=run_env,
                        timeout=5.0,
                        check=False,
                        **probe_run_kwargs,
                    )
                    ub_err = (ub_run.stderr or "") + (ub_run.stdout or "")
                    ubsan_verified = ub_run.returncode != 0 and (
                        "runtime error:" in ub_err or "UndefinedBehaviorSanitizer" in ub_err
                    )
                except (OSError, subprocess.SubprocessError):
                    ubsan_verified = False
                if not ubsan_verified:
                    attempts.append(
                        {"profile": prof_name, "stage": "negative_control_ubsan", "failed": True}
                    )
                    continue

            info: dict[str, Any] = {
                "available": True,
                "compiler": eff_compiler,
                "platform": sys.platform,
                "profile": prof_name,
                "flags": list(flags),
                "env": dict(env_overrides),
                "asan_verified": asan_verified,
                "ubsan_verified": ubsan_verified,
                "negative_control_passed": True,
                "preload_detected": preload_detected,
            }
            if use_cache:
                _SANITIZER_PROBE_CACHE[cache_key] = _clone_sanitizer_info(info)
            return info

    reason = (
        "ASan/UBSan not supported by this Windows compiler toolchain"
        if sys.platform == "win32"
        else f"No sanitizer profile passed compile + negative-control probe for {eff_compiler}"
    )
    return {
        "available": False,
        "compiler": eff_compiler,
        "platform": sys.platform,
        "profile": None,
        "flags": [],
        "env": {},
        "asan_verified": False,
        "ubsan_verified": False,
        "negative_control_passed": False,
        "preload_detected": preload_detected,
        "attempts": attempts,
        "reason": reason,
    }


async def probe_sanitizer_support_async(
    compiler: str = "g++",
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Async wrapper around ``probe_sanitizer_support``."""
    return await asyncio.to_thread(probe_sanitizer_support, compiler, use_cache=use_cache)


def validate_compiler_spec(compiler: str) -> str:
    """Validate a C++ compiler command name or absolute path against a strict allowlist.

    Blocks control characters, relative paths with directory separators (e.g. ``./evil.sh``),
    and arbitrary non-compiler executables (e.g. ``/bin/sh``, ``python3``).
    """
    if not isinstance(compiler, str):
        raise ValueError("compiler must be a string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in compiler):
        raise ValueError("compiler contains invalid control characters")
    cleaned = compiler.strip()
    if not cleaned:
        raise ValueError("compiler must be a non-empty string")

    has_sep = "/" in cleaned or "\\" in cleaned
    is_abs = os.path.isabs(cleaned) or bool(_WINDOWS_DRIVE_PREFIX_RE.match(cleaned))
    if has_sep and not is_abs:
        raise ValueError(
            f"relative compiler path with directory separators is not allowed: {compiler!r}; "
            "use a bare compiler name (e.g. 'g++-14') or an absolute path"
        )
    if is_abs:
        parts = re.split(r"[\\/]", cleaned)
        if any(part == ".." for part in parts):
            raise ValueError(f"compiler path must not contain '..' segments: {compiler!r}")
    basename = os.path.basename(cleaned.replace("\\", "/"))
    if not basename or not _ALLOWED_COMPILER_BASENAME_RE.match(basename):
        raise ValueError(
            f"unsupported C++ compiler executable {compiler!r}; expected a g++/clang++/c++/icpx binary"
        )
    return cleaned


def has_bits_stdcpp_include(code: str) -> bool:
    """Detect whether C++ source includes GCC-only <bits/stdc++.h>."""
    return bool(_BITS_STDCPP_RE.search(code))


def _find_suggested_gcc_compilers() -> list[str]:
    """Probe common GNU g++ versioned binaries (e.g. Homebrew g++-14 on macOS)."""
    candidates = ("g++-15", "g++-14", "g++-13", "g++-12", "g++-11")
    extra_dirs = ("/opt/homebrew/bin", "/usr/local/bin")
    found: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        path = shutil.which(name)
        if not path:
            for d in extra_dirs:
                candidate = os.path.join(d, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    path = candidate
                    break
        if path and path not in seen:
            seen.add(path)
            found.append(path)
    return found


def resolve_effective_compiler(
    explicit_compiler: str | None,
    manifest: Any | None = None,
    *,
    default: str = "g++",
) -> str:
    """Resolve and validate C++ compiler priority: explicit arg > manifest.compiler > default ('g++')."""
    if explicit_compiler is not None:
        if not isinstance(explicit_compiler, str):
            raise ValueError("compiler must be a string")
        if explicit_compiler.strip():
            return validate_compiler_spec(explicit_compiler)
    manifest_compiler = getattr(manifest, "compiler", None) if manifest is not None else None
    if isinstance(manifest_compiler, str) and manifest_compiler.strip():
        return validate_compiler_spec(manifest_compiler)
    return validate_compiler_spec(default)


def _clone_compiler_info(info: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(info)
    cloned["suggested_gcc_compilers"] = list(info.get("suggested_gcc_compilers", []))
    return cloned


def detect_compiler_info(compiler: str = "g++", *, use_cache: bool = True) -> dict[str, Any]:
    """Inspect a C++ compiler binary to detect Apple Clang / GNU GCC and <bits/stdc++.h> support."""
    try:
        key = validate_compiler_spec((compiler or "g++").strip() or "g++")
    except ValueError as exc:
        return {
            "compiler": str(compiler),
            "resolved_path": None,
            "available": False,
            "is_apple_clang": False,
            "is_clang": False,
            "is_gcc": False,
            "version_line": "",
            "supports_bits_stdcpp": False,
            "suggested_gcc_compilers": [],
            "warning": str(exc),
        }

    has_sep = "/" in key or "\\" in key
    resolved = shutil.which(key)
    if resolved is None and has_sep and os.path.isfile(key) and (os.name == "nt" or os.access(key, os.X_OK)):
        resolved = key
    cache_key = resolved or key
    if use_cache and cache_key in _COMPILER_INFO_CACHE:
        return _clone_compiler_info(_COMPILER_INFO_CACHE[cache_key])

    info: dict[str, Any] = {
        "compiler": key,
        "resolved_path": resolved,
        "available": False,
        "is_apple_clang": False,
        "is_clang": False,
        "is_gcc": False,
        "version_line": "",
        "supports_bits_stdcpp": False,
        "suggested_gcc_compilers": [],
        "warning": None,
    }
    try:
        proc = subprocess.run(
            [key, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.0,
            check=False,
        )
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        first_line = combined.splitlines()[0].strip() if combined else ""
        lower = combined.lower()
        is_apple_clang = "apple clang" in lower or ("clang" in lower and "apple" in lower)
        is_clang = "clang" in lower
        is_gcc = (not is_clang) and (
            "free software foundation" in lower or "gcc" in lower or "g++" in lower
        )
        available = proc.returncode == 0
        info["available"] = available
        info["version_line"] = first_line
        info["is_apple_clang"] = is_apple_clang
        info["is_clang"] = is_clang
        info["is_gcc"] = is_gcc
        info["supports_bits_stdcpp"] = available and not is_apple_clang
        if available and is_apple_clang:
            suggested = _find_suggested_gcc_compilers()
            info["suggested_gcc_compilers"] = suggested
            if suggested:
                gcc_hint = (
                    f"Detected GNU GCC alternative(s): {', '.join(suggested)}. "
                    f"You can set \"compiler\": \"{suggested[0]}\" in .autocode/manifest.json "
                    "or pass compiler=..., or use standard C++20 headers instead of <bits/stdc++.h>."
                )
            else:
                gcc_hint = (
                    "Use standard C++20 headers (<iostream>, <vector>, <algorithm>, etc.) "
                    "instead of <bits/stdc++.h>, or install Homebrew GCC (e.g. `brew install gcc`) "
                    "and set \"compiler\": \"g++-14\" in .autocode/manifest.json."
                )
            info["warning"] = (
                f"Compiler '{key}' is Apple Clang ({first_line}), which does not support "
                f"GCC-only <bits/stdc++.h>. {gcc_hint}"
            )
        elif not available:
            info["warning"] = f"Compiler '{key}' exited with code {proc.returncode} on --version."
    except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError):
        info["available"] = False
        info["supports_bits_stdcpp"] = False
        info["warning"] = f"Compiler '{key}' not found or not executable."

    # Only cache positive availability results so installing a compiler mid-session takes effect
    if use_cache and info["available"]:
        _COMPILER_INFO_CACHE[cache_key] = _clone_compiler_info(info)
    return _clone_compiler_info(info)


async def detect_compiler_info_async(compiler: str = "g++", *, use_cache: bool = True) -> dict[str, Any]:
    """Async wrapper around ``detect_compiler_info``."""
    return await asyncio.to_thread(detect_compiler_info, compiler, use_cache=use_cache)


@dataclass
class BuildSpec:
    """单个源文件的并发编译规格（供 ``compile_all`` 使用）。"""

    source: str
    binary: str | None = None
    include_dirs: list[str] | None = None
    extra_sources: list[str] | None = None
    compiler: str = "g++"
    std: str = "c++20"
    opt_level: str = "O2"
    timeout: int = 30


@dataclass
class RunResult:
    """执行结果。"""

    success: bool
    return_code: int = -1
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    timed_out: bool = False
    time_ms: int = 0
    memory_limit_exceeded: bool = False
    memory_mb: float | None = None


def get_work_dir(problem_dir: str, tool_name: str) -> str:
    """
    每次调用生成独立工作目录。

    Args:
        problem_dir: 题目目录
        tool_name: 工具名称

    Returns:
        临时工作目录路径
    """
    run_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(problem_dir, ".tmp", f"{tool_name}_{run_id}")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def cleanup_work_dir(work_dir: str) -> None:
    """清理工作目录。"""
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)


@functools.lru_cache(maxsize=8)
def _resolve_compiler(compiler: str) -> str:
    """Resolve `g++` on macOS away from Apple Clang toward Homebrew GCC if installed.

    Apple's `/usr/bin/g++` is a Clang shim lacking GNU `<bits/stdc++.h>`. When the
    caller requests the default `"g++"` on darwin, probe `g++-15`..`g++-10` via
    PATH and standard Homebrew prefixes and return the first working GCC binary.
    """
    if sys.platform != "darwin" or compiler != "g++":
        return compiler
    resolved = shutil.which(compiler) or compiler
    try:
        probe = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        banner = f"{probe.stdout}\n{probe.stderr}".lower()
        if "clang" not in banner:
            return compiler
    except (OSError, subprocess.SubprocessError):
        return compiler

    for candidate in ("g++-15", "g++-14", "g++-13", "g++-12", "g++-11", "g++-10"):
        found = shutil.which(candidate)
        if found:
            return found
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            full_path = os.path.join(prefix, candidate)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                return full_path
    return compiler


def _has_local_headers(
    include_dirs: list[str] | None,
    source_path: str | None = None,
) -> bool:
    """Check whether any include directory or source directory contains local C/C++ headers."""
    dirs_to_check: list[str] = list(include_dirs or [])
    if source_path:
        src_dir = os.path.dirname(os.path.abspath(source_path))
        if src_dir:
            dirs_to_check.append(src_dir)
    if not dirs_to_check:
        return False
    templates_real = os.path.realpath(TEMPLATES_DIR) if os.path.exists(TEMPLATES_DIR) else ""
    header_exts = (".h", ".hpp", ".hh", ".hxx")
    seen_real: set[str] = set()
    for inc_dir in dirs_to_check:
        if not inc_dir or not os.path.isdir(inc_dir):
            continue
        real_dir = os.path.normcase(os.path.realpath(inc_dir))
        if real_dir in seen_real:
            continue
        seen_real.add(real_dir)
        if templates_real and real_dir == os.path.normcase(templates_real):
            continue
        try:
            for entry in os.listdir(inc_dir):
                lower = entry.lower()
                if lower.endswith(header_exts) and lower != "testlib.h":
                    return True
        except OSError:
            return True
    return False


async def compile_cpp(
    source_path: str,
    binary_path: str,
    timeout: int = 30,
    compiler: str = "g++",
    std: str = "c++20",
    opt_level: str = "O2",
    include_dirs: list[str] | None = None,
    extra_sources: list[str] | None = None,
    use_cache: bool = True,
    sanitizers: bool = False,
    extra_flags: list[str] | None = None,
) -> CompileResult:
    """
    编译 C++ 文件（支持多文件链接，如 grader_linked 通信题），带超时控制。
    """
    if not os.path.exists(source_path):
        return CompileResult(
            success=False,
            error=f"Source file not found: {source_path}",
        )

    normalized_extra = list(extra_sources or [])
    for extra_src in normalized_extra:
        if not os.path.exists(extra_src):
            return CompileResult(
                success=False,
                error=f"Extra source file not found: {extra_src}",
            )

    try:
        validated_compiler = validate_compiler_spec(compiler)
    except ValueError as exc:
        return CompileResult(success=False, error=f"Invalid compiler: {exc}")

    effective_compiler = _resolve_compiler(validated_compiler)
    compile_extra_flags: list[str] = []
    for raw_flag in extra_flags or []:
        if not isinstance(raw_flag, str) or raw_flag not in _ALLOWED_EXTRA_FLAGS:
            return CompileResult(
                success=False,
                error=f"Disallowed compiler extra_flag: {raw_flag!r}",
            )
        if raw_flag not in compile_extra_flags:
            compile_extra_flags.append(raw_flag)
    sanitizer_info: dict[str, Any] | None = None
    if sanitizers:
        sanitizer_info = await probe_sanitizer_support_async(effective_compiler)
        if not sanitizer_info.get("available"):
            return CompileResult(
                success=False,
                error=f"Sanitizer build unavailable for {effective_compiler}: {sanitizer_info.get('reason', 'probe failed')}",
                stderr=str(sanitizer_info.get("attempts", "")),
                sanitizer_info=sanitizer_info,
            )
        for flag in sanitizer_info.get("flags", []):
            if flag in _ALLOWED_EXTRA_FLAGS and flag not in compile_extra_flags:
                compile_extra_flags.append(flag)

    effective_use_cache = (
        use_cache and not normalized_extra and not _has_local_headers(include_dirs, source_path)
    )

    if effective_use_cache:
        cached_binary = _cache.get(
            source_path,
            effective_compiler,
            std,
            opt_level,
            extra_flags=compile_extra_flags or None,
        )
        if cached_binary:
            output_dir = os.path.dirname(binary_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            shutil.copy2(cached_binary, binary_path)
            try:
                os.utime(binary_path, None)
            except OSError:
                pass
            if sys.platform != "win32":
                try:
                    os.chmod(binary_path, os.stat(binary_path).st_mode | 0o111)
                except OSError:
                    pass
            return CompileResult(
                success=True,
                binary_path=binary_path,
                stderr="(cached)",
                sanitizer_info=sanitizer_info,
            )

    # 确保输出目录存在
    output_dir = os.path.dirname(binary_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # 获取 templates 目录路径（用于 testlib.h）
    templates_dir = TEMPLATES_DIR

    # 构建 include 参数
    include_flags = []
    if include_dirs:
        for inc_dir in include_dirs:
            include_flags.extend(["-I", inc_dir])
    # 添加 templates 目录
    if os.path.exists(templates_dir):
        include_flags.extend(["-isystem", templates_dir])

    cmd = [
        effective_compiler,
        f"-std={std}",
        f"-{opt_level}",
        "-Wall",
        "-Wextra",
        *compile_extra_flags,
        *include_flags,
        source_path,
        *normalized_extra,
        "-o",
        binary_path,
    ]

    # Windows 上使用静态链接避免 DLL 版本冲突（特别是 testlib.h 程序）；sanitizer 模式跳过 -static
    if sys.platform == "win32" and not sanitizers:
        cmd.append("-static")

    popen_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **popen_kwargs,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError) as e:
            if process.pid:
                await terminate_pid_tree(process.pid)
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except Exception:
                pass
            if isinstance(e, asyncio.CancelledError):
                raise
            return CompileResult(
                success=False,
                error=f"Compilation timeout after {timeout}s. Consider increasing timeout or simplifying source code.",
            )

        if process.returncode != 0:
            out_text = stdout.decode("utf-8", errors="replace")
            err_text = stderr.decode("utf-8", errors="replace")
            hint: str | None = None
            error_msg = f"Compilation failed with code {process.returncode}"
            if "bits/stdc++.h" in err_text:
                comp_info = await detect_compiler_info_async(effective_compiler)
                if comp_info.get("is_apple_clang") and comp_info.get("warning"):
                    hint = str(comp_info["warning"])
                    error_msg = f"{error_msg}. {hint}"
                if sys.platform == "darwin":
                    err_text = (
                        f"{err_text}\n"
                        "Hint (macOS): Apple Clang does not include <bits/stdc++.h>. "
                        "Install Homebrew GCC (`brew install gcc`) so `g++-14`/`g++-13` is available."
                    )
            return CompileResult(
                success=False,
                error=error_msg,
                stdout=out_text,
                stderr=err_text,
                hint=hint,
            )

        if effective_use_cache:
            _cache.set(
                source_path,
                binary_path,
                effective_compiler,
                std,
                opt_level,
                extra_flags=compile_extra_flags or None,
            )

        return CompileResult(
            success=True,
            binary_path=binary_path,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            sanitizer_info=sanitizer_info,
        )

    except FileNotFoundError:
        return CompileResult(
            success=False,
            error=f"Compiler not found: {effective_compiler}",
        )
    except Exception as e:
        return CompileResult(
            success=False,
            error=f"Compilation error: {str(e)}",
        )


def _clamp_rlimit_bytes(memory_bytes: int, limit_attr: str) -> int:
    """将请求的内存上限钳制到当前进程的 POSIX hard limit 以内，避免 EPERM/ValueError。"""
    if _resource is None:
        return memory_bytes

    limit_const = getattr(_resource, limit_attr, None)
    if limit_const is None:
        return memory_bytes
    try:
        _, cur_hard = cast(Any, _resource).getrlimit(limit_const)
    except (ValueError, OSError):
        return memory_bytes
    rlim_inf = getattr(_resource, "RLIM_INFINITY", -1)
    if cur_hard != rlim_inf and cur_hard >= 0:
        return min(memory_bytes, int(cur_hard))
    return memory_bytes


def _set_posix_resource_limit(memory_mb: int | float) -> None:
    """POSIX 上设置进程资源限制（通过 preexec_fn 调用，并强制禁用 core dump）。"""
    disable_posix_core_dumps()
    if _resource is None:
        return
    requested_bytes = max(1, int(round(float(memory_mb) * 1024 * 1024)))
    for limit_attr in ("RLIMIT_AS", "RLIMIT_DATA"):
        limit_const = getattr(_resource, limit_attr, None)
        if limit_const is None:
            continue
        clamped = _clamp_rlimit_bytes(requested_bytes, limit_attr)
        try:
            cast(Any, _resource).setrlimit(limit_const, (clamped, clamped))
        except (ValueError, OSError):
            pass


def _set_macos_resource_limit(memory_mb: int | float) -> None:
    """macOS 上设置进程资源限制（兼容入口，委托给 POSIX 实现）。"""
    _set_posix_resource_limit(memory_mb)


async def _force_terminate_process(
    process: asyncio.subprocess.Process,
    job: WinJobObject | None = None,
) -> None:
    """强制终止进程，确保不会卡住。

    Args:
        process: 要终止的进程
        job: Windows Job Object（可选）
    """
    # 先尝试 Job Object 终止（Windows 上更可靠）
    if job:
        try:
            job.terminate()
        except (OSError, RuntimeError) as e:
            _logger.debug("Job object terminate failed: %s", e)

    already_gone = False
    try:
        if process.pid:
            await terminate_pid_tree(process.pid)
    finally:
        # 再尝试直接 kill；即使 terminate_pid_tree 被取消也确保清理子进程句柄。
        try:
            process.kill()
        except ProcessLookupError:
            already_gone = True
        except OSError as e:
            _logger.debug("Failed to kill process: %s", e)

    if already_gone:
        return

    # 等待进程退出，设置超时防止卡住
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except (asyncio.TimeoutError, TimeoutError):
        # 如果 2 秒后进程仍未退出，再次尝试强制终止
        try:
            process.kill()
        except ProcessLookupError:
            return
        except OSError as e:
            _logger.debug("Second kill attempt failed: %s", e)


def _classify_memory_limit_exceeded(
    *,
    return_code: int,
    stderr_text: str,
    memory_limit_mb: int | float,
    peak_rss_mb: float | None,
    rss_limit_exceeded: bool,
    disable_as_limit: bool,
) -> bool:
    """判定进程失败是否为真实的内存超限（MLE）。

    避免将低 RSS 下由外部 kill -9 / 容器拦截触发的 SIGKILL (-9 / 137) 误归因成 MLE。
    """
    if rss_limit_exceeded:
        return True
    limit_mb = max(1.0, float(memory_limit_mb))
    if not disable_as_limit and peak_rss_mb is not None and peak_rss_mb > limit_mb:
        return True
    # Windows STATUS_NO_MEMORY (0xC0000017)
    if return_code in (3221225495, -1073741801):
        return True
    lowered_err = stderr_text.lower()
    if (
        "bad_alloc" in stderr_text
        or "out of memory" in lowered_err
        or "cannot allocate memory" in lowered_err
        or "failed to map segment" in lowered_err
    ):
        return True
    sigkill_rc = return_code in (-9, 137) or (
        hasattr(signal, "SIGKILL") and return_code == -signal.SIGKILL
    )
    if sigkill_rc and not disable_as_limit and peak_rss_mb is not None:
        return peak_rss_mb >= limit_mb * 0.85
    return False


async def _run_process(
    cmd: list[str],
    stdin: str = "",
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    process_start_hook: Callable[[int], None] | None = None,
    *,
    env: dict[str, str] | None = None,
    disable_as_limit: bool = False,
    cwd: str | None = None,
) -> RunResult:
    """运行进程的公共逻辑。"""
    import tempfile

    start_time = time.perf_counter()
    effective_limit_mb = max(1.0, float(memory_mb))
    safe_memory_mb = max(1, int(round(effective_limit_mb)))
    job = None
    process = None
    out_f = err_f = None
    stdout_path = stderr_path = None
    proc_env: dict[str, str] | None = None
    if env:
        proc_env = dict(os.environ)
        proc_env.update(env)

    try:
        # 大输出重定向到临时文件，避免全量读入内存：运行期间输出由 OS 落盘，
        # 峰值内存仅受单次写入块大小限制，而非整份输出。
        out_f = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".autocode.out")
        err_f = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".autocode.err")
        stdout_path, stderr_path = out_f.name, err_f.name

        if sys.platform == "win32":
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                env=proc_env,
                cwd=cwd,
            )
            if process.pid:
                try:
                    from .win_job import WinJobObject

                    job = WinJobObject(
                        memory_mb=None if disable_as_limit else safe_memory_mb,
                        timeout_sec=timeout,
                    )
                    job.assign_process(process.pid)
                except (OSError, RuntimeError, TypeError) as e:
                    if job is not None:
                        job.close()
                        job = None
                    err_msg = str(e)
                    is_expected_short_lived = (
                        not is_pid_alive(process.pid)
                        or "Access is denied" in err_msg
                        or "拒绝访问" in err_msg
                        or "(5," in err_msg
                    )
                    log_fn = _logger.debug if is_expected_short_lived else _logger.warning
                    log_fn(
                        "Failed to setup Windows Job Object (pid=%s, cmd=%s): %s",
                        process.pid,
                        cmd[0] if cmd else "",
                        e,
                    )
        elif sys.platform == "darwin":
            # macOS: 使用 preexec_fn 设置资源限制；sanitizer 模式下跳过 RLIMIT_AS 但仍禁用 core dump
            darwin_kwargs: dict[str, Any] = {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": out_f,
                "stderr": err_f,
                "start_new_session": True,
                "env": proc_env,
                "cwd": cwd,
                "preexec_fn": (
                    disable_posix_core_dumps
                    if disable_as_limit
                    else (lambda: _set_posix_resource_limit(safe_memory_mb))
                ),
            }
            process = await asyncio.create_subprocess_exec(
                *cmd,
                **darwin_kwargs,
            )
        else:
            # Linux: 优先使用 prlimit（含 --core=0，避免 preexec_fn）；无 prlimit 时回退 preexec_fn
            if disable_as_limit:
                prlimit_path = shutil.which("prlimit")
                if prlimit_path:
                    process = await asyncio.create_subprocess_exec(
                        prlimit_path,
                        "--core=0",
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=out_f,
                        stderr=err_f,
                        start_new_session=True,
                        env=proc_env,
                        cwd=cwd,
                    )
                else:
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=out_f,
                        stderr=err_f,
                        start_new_session=True,
                        env=proc_env,
                        cwd=cwd,
                        preexec_fn=disable_posix_core_dumps,
                    )
            else:
                memory_bytes = safe_memory_mb * 1024 * 1024
                prlimit_path = shutil.which("prlimit")
                if prlimit_path:
                    as_bytes = _clamp_rlimit_bytes(memory_bytes, "RLIMIT_AS")
                    data_bytes = _clamp_rlimit_bytes(memory_bytes, "RLIMIT_DATA")
                    process = await asyncio.create_subprocess_exec(
                        prlimit_path,
                        f"--as={as_bytes}",
                        f"--data={data_bytes}",
                        "--core=0",
                        *cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=out_f,
                        stderr=err_f,
                        start_new_session=True,
                        env=proc_env,
                        cwd=cwd,
                    )
                else:
                    exec_kwargs: dict[str, Any] = {
                        "stdin": asyncio.subprocess.PIPE,
                        "stdout": out_f,
                        "stderr": err_f,
                        "start_new_session": True,
                        "env": proc_env,
                        "cwd": cwd,
                    }
                    if os.name != "nt":
                        exec_kwargs["preexec_fn"] = lambda: _set_posix_resource_limit(safe_memory_mb)
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        **exec_kwargs,
                    )

        if process and process.pid and process_start_hook:
            try:
                process_start_hook(process.pid)
            except Exception as e:
                _logger.debug("process_start_hook failed: %s", e)

        peak_rss_mb: float | None = None
        rss_limit_exceeded = False
        rss_task: asyncio.Task[None] | None = None
        if process and process.pid and _psutil is not None:
            async def _sample_rss(pid: int) -> None:
                nonlocal peak_rss_mb, rss_limit_exceeded
                assert _psutil is not None
                try:
                    p_obj = _psutil.Process(pid)
                    while True:
                        try:
                            rss = p_obj.memory_info().rss / (1024.0 * 1024.0)
                            if peak_rss_mb is None or rss > peak_rss_mb:
                                peak_rss_mb = rss
                            if not disable_as_limit and rss > effective_limit_mb:
                                rss_limit_exceeded = True
                                await _force_terminate_process(process, job)
                                break
                        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                            break
                        await asyncio.sleep(0.005)
                except Exception:
                    return

            rss_task = asyncio.create_task(_sample_rss(process.pid))

        try:
            # Windows 上 testlib strict 模式期望 CRLF 换行符
            # 将 LF 转换为 CRLF 以满足 validator 的 readEoln() 要求
            processed_stdin = stdin
            if sys.platform == "win32" and stdin:
                processed_stdin = _normalize_windows_stdin(stdin)

            await asyncio.wait_for(
                process.communicate(input=(processed_stdin or "").encode("utf-8")),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            # 调用被取消时也必须强制清理子进程，防止残留。
            if rss_task is not None:
                rss_task.cancel()
            await _force_terminate_process(process, job)
            raise
        except (asyncio.TimeoutError, TimeoutError):
            # 超时时强制终止进程
            if rss_task is not None:
                rss_task.cancel()
            await _force_terminate_process(process, job)
            for _timeout_fh in (out_f, err_f):
                if _timeout_fh is not None:
                    try:
                        _timeout_fh.close()
                    except OSError:
                        pass
            out_str = ""
            err_str = ""
            if stdout_path and os.path.exists(stdout_path):
                try:
                    with open(stdout_path, "rb") as _out:
                        out_str = _out.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
                except OSError:
                    pass
            if stderr_path and os.path.exists(stderr_path):
                try:
                    with open(stderr_path, "rb") as _err:
                        err_str = _err.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
                except OSError:
                    pass
            return RunResult(
                success=False,
                timed_out=True,
                error=f"Execution timeout after {timeout}s. The program may contain an infinite loop or the input data may be too large.",
                stdout=out_str,
                stderr=err_str,
                time_ms=int(round((time.perf_counter() - start_time) * 1000)),
                memory_mb=round(peak_rss_mb, 2) if peak_rss_mb is not None else None,
            )
        finally:
            if rss_task is not None and not rss_task.done():
                rss_task.cancel()
                try:
                    await rss_task
                except asyncio.CancelledError:
                    pass

        elapsed_ms = int(round((time.perf_counter() - start_time) * 1000))

        # 正常完成后关闭 Job Handle；若 Job 配置了 KILL_ON_JOB_CLOSE，
        # 关闭时仍可能终止 Job 中尚未退出的子进程。
        if job:
            job.close()

        out_f.close()
        err_f.close()
        with open(stdout_path, "rb") as _out, open(stderr_path, "rb") as _err:
            stdout_data = _out.read(10 * 1024 * 1024)
            stderr_data = _err.read(10 * 1024 * 1024)
        ret = process.returncode if process.returncode is not None else -1
        out_str = stdout_data.decode("utf-8", errors="replace")
        err_str = stderr_data.decode("utf-8", errors="replace")
        mle = _classify_memory_limit_exceeded(
            return_code=ret,
            stderr_text=err_str,
            memory_limit_mb=effective_limit_mb,
            peak_rss_mb=peak_rss_mb,
            rss_limit_exceeded=rss_limit_exceeded,
            disable_as_limit=disable_as_limit,
        )
        return RunResult(
            success=(ret == 0 and not mle),
            return_code=ret,
            stdout=out_str,
            stderr=err_str,
            time_ms=elapsed_ms,
            memory_limit_exceeded=mle,
            memory_mb=round(peak_rss_mb, 2) if peak_rss_mb is not None else None,
        )

    except FileNotFoundError:
        return RunResult(
            success=False,
            error=f"Binary not found or prlimit unavailable: {cmd[0]}",
        )
    except Exception as e:
        # 最后防线：捕获所有异常确保进程被终止，防止资源泄漏
        if process:
            await _force_terminate_process(process, job)
        return RunResult(
            success=False,
            error=f"Execution error: {str(e)}",
        )
    finally:
        for _fh in (out_f, err_f):
            if _fh is not None:
                try:
                    _fh.close()
                except OSError:
                    pass
        for _p in (stdout_path, stderr_path):
            if _p and os.path.exists(_p):
                try:
                    os.remove(_p)
                except OSError:
                    pass


async def run_binary(
    binary_path: str,
    stdin: str = "",
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    process_start_hook: Callable[[int], None] | None = None,
    *,
    env: dict[str, str] | None = None,
    disable_as_limit: bool = False,
    cwd: str | None = None,
) -> RunResult:
    """运行二进制文件，带超时和内存限制。"""
    if not os.path.exists(binary_path):
        return RunResult(
            success=False,
            error=f"Binary not found: {binary_path}",
        )

    abs_bin = os.path.abspath(binary_path)
    return await _run_process(
        [abs_bin],
        stdin,
        timeout,
        memory_mb,
        process_start_hook,
        env=env,
        disable_as_limit=disable_as_limit,
        cwd=cwd,
    )


async def run_binary_with_args(
    binary_path: str,
    args: list[str],
    stdin: str = "",
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    process_start_hook: Callable[[int], None] | None = None,
    *,
    env: dict[str, str] | None = None,
    disable_as_limit: bool = False,
) -> RunResult:
    """运行二进制文件并传递命令行参数。"""
    if not os.path.exists(binary_path):
        return RunResult(
            success=False,
            error=f"Binary not found: {binary_path}",
        )

    abs_bin = os.path.abspath(binary_path)
    return await _run_process(
        [abs_bin, *args],
        stdin,
        timeout,
        memory_mb,
        process_start_hook,
        env=env,
        disable_as_limit=disable_as_limit,
    )


T = TypeVar("T")
R = TypeVar("R")


async def run_batch(
    items: list[T],
    worker: Callable[[T], Awaitable[R]],
    limit: int = 4,
) -> list[R]:
    """使用有上限的并发运行 ``worker``，保持输入顺序。

    用于把原本严格串行的多子进程步骤（生成/校验/对拍等）改为有限并发，
    在不改变单步正确性、超时与失败语义的前提下提升吞吐。

    Args:
        items: 待处理的输入项列表
        worker: 单项的异步处理函数
        limit: 最大并发数（<=0 时退化为 1）

    Returns:
        与 ``items`` 顺序一致的 ``worker`` 返回值列表
    """
    safe_limit = max(1, int(limit))
    if not items:
        return []
    if safe_limit >= len(items):
        return await asyncio.gather(*(worker(it) for it in items))
    semaphore = asyncio.Semaphore(safe_limit)

    async def bounded(item: T) -> R:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(bounded(it) for it in items))


async def compile_all(
    problem_dir: str,
    specs: Sequence[str | BuildSpec],
    max_concurrent: int = 4,
) -> dict[str, CompileResult]:
    """
    并发编译多个源文件。

    源路径相对于 ``problem_dir``（或绝对路径）。默认输出二进制与源文件同目录、
    同名换扩展名；可通过 :class:`BuildSpec` 指定输出路径、include 目录与编译选项。

    Args:
        problem_dir: 题目目录（用于解析相对路径与默认输出目录）
        specs: 源文件名或 :class:`BuildSpec` 列表（如 ["gen.cpp", "val.cpp"]）
        max_concurrent: 最大并发编译数

    Returns:
        dict: 传入的源名/规格 source -> 编译结果
    """
    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    exe_ext = get_exe_extension()

    def _resolve(spec: str | BuildSpec) -> BuildSpec:
        if isinstance(spec, str):
            return BuildSpec(source=spec)
        return spec

    async def compile_one(spec: BuildSpec) -> tuple[str, CompileResult]:
        async with semaphore:
            source_key = spec.source
            source_path = spec.source if os.path.isabs(spec.source) else os.path.join(problem_dir, spec.source)
            if not os.path.exists(source_path):
                return source_key, CompileResult(
                    success=False,
                    error=f"Source file not found: {source_path}",
                )
            if spec.binary:
                binary_path = spec.binary if os.path.isabs(spec.binary) else os.path.join(problem_dir, spec.binary)
            else:
                binary_path = os.path.splitext(source_path)[0] + exe_ext
            resolved_extra = (
                [
                    p if os.path.isabs(p) else os.path.join(problem_dir, p)
                    for p in spec.extra_sources
                ]
                if spec.extra_sources
                else None
            )
            result = await compile_cpp(
                source_path,
                binary_path,
                timeout=spec.timeout,
                compiler=spec.compiler,
                std=spec.std,
                opt_level=spec.opt_level,
                include_dirs=spec.include_dirs,
                extra_sources=resolved_extra,
            )
            return source_key, result

    tasks = [compile_one(_resolve(s)) for s in specs]
    results = await asyncio.gather(*tasks)
    return dict(results)
