"""Multi-language compilation, syntax checking, multiplier resolution, and sandboxed execution."""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .compiler import CompileResult, RunResult, _run_process, compile_cpp
from .platform import get_exe_extension
from .sandbox import build_runner_ro_paths, get_active_sandbox_executor, resolve_wrapped_invocation

SupportedLanguage = Literal["cpp", "python", "pypy", "rust", "java"]

SUPPORTED_LANGUAGES: tuple[SupportedLanguage, ...] = (
    "cpp",
    "python",
    "pypy",
    "rust",
    "java",
)

DEFAULT_TIME_LIMIT_MULTIPLIERS: dict[str, float] = {
    "cpp": 1.0,
    "rust": 1.0,
    "java": 2.0,
    "pypy": 2.5,
    "python": 5.0,
}

_LANGUAGE_ALIASES: dict[str, SupportedLanguage] = {
    "cpp": "cpp",
    "c++": "cpp",
    "python": "python",
    "python3": "python",
    "py": "python",
    "py3": "python",
    "pypy": "pypy",
    "pypy3": "pypy",
    "rust": "rust",
    "rs": "rust",
    "java": "java",
}

_EXTENSION_TO_LANGUAGE: dict[str, SupportedLanguage] = {
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".cp": "cpp",
    ".py": "python",
    ".pyw": "python",
    ".rs": "rust",
    ".java": "java",
}

_JAVA_PUBLIC_CLASS_RE = re.compile(r"\bpublic\s+class\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_JAVA_CLASS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True)
class PreparedTarget:
    """Prepared executable or validated script artifact for a target language."""

    language: SupportedLanguage
    source_path: str
    artifact_path: str
    main_class: str | None = None
    runtime_executable: str | None = None


def _validate_positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a positive finite number, got {value!r}")
    num = float(value)
    if not math.isfinite(num) or num <= 0.0:
        raise ValueError(f"{label} must be a positive finite number, got {value!r}")
    return num


def _validate_safe_executable_spec(spec: str, label: str) -> str:
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in spec):
        raise ValueError(f"{label} contains invalid control characters")
    cleaned = spec.strip()
    if any(seg == ".." for seg in re.split(r"[/\\]+", cleaned)):
        raise ValueError(f"{label} must not contain '..' path traversal segments")
    return cleaned


def _validate_safe_path_str(path: str | os.PathLike[str], label: str = "path") -> str:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} must be a non-empty path")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError(f"{label} contains invalid control characters")
    return raw


def normalize_language(language: str) -> SupportedLanguage:
    """Normalize a language identifier/alias to a canonical supported language."""
    if not isinstance(language, str) or not language.strip():
        raise ValueError(f"Unsupported language: {language!r}")
    cleaned = language.strip().lower()
    canonical = _LANGUAGE_ALIASES.get(cleaned)
    if canonical is None:
        supported = ", ".join(SUPPORTED_LANGUAGES)
        raise ValueError(f"Unsupported language {language!r}; expected one of: {supported}")
    return canonical


def infer_language_from_path(path: str | os.PathLike[str]) -> SupportedLanguage | None:
    """Infer canonical language from a file extension, or return None if unrecognized."""
    raw = _validate_safe_path_str(path, "path")
    suffix = Path(raw).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(suffix)


def _extract_manifest_multipliers(manifest: Any | None) -> Mapping[Any, Any] | None:
    if manifest is None:
        return None
    if isinstance(manifest, Mapping):
        raw = manifest.get("time_limit_multipliers")
    else:
        raw = getattr(manifest, "time_limit_multipliers", None)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("manifest.time_limit_multipliers must be a mapping")
    return raw


def resolve_time_limit_multiplier(
    language: str,
    manifest: Any | None = None,
) -> float:
    """Resolve OJ time-limit multiplier for ``language`` with manifest override support.

    Defaults: ``cpp: 1.0``, ``rust: 1.0``, ``java: 2.0``, ``pypy: 2.5``, ``python: 5.0``.
    Explicit ``manifest.time_limit_multipliers`` entries are strictly validated against
    booleans, NaN, infinities, and non-positive values.
    """
    canonical = normalize_language(language)
    raw_multipliers = _extract_manifest_multipliers(manifest)
    if raw_multipliers is not None:
        exact_overrides: dict[str, float] = {}
        alias_overrides: dict[SupportedLanguage, float] = {}
        for raw_key, raw_val in raw_multipliers.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ValueError("time_limit_multipliers keys must be non-empty strings")
            if any(ch in raw_key for ch in ("\x00", "\n", "\r")):
                raise ValueError("time_limit_multipliers key contains invalid control characters")
            norm_key = raw_key.strip().lower()
            mult = _validate_positive_number(raw_val, f"time_limit_multipliers[{raw_key!r}]")
            exact_overrides[norm_key] = mult
            aliased = _LANGUAGE_ALIASES.get(norm_key)
            if aliased is not None:
                alias_overrides.setdefault(aliased, mult)
        if canonical in exact_overrides:
            return exact_overrides[canonical]
        if canonical in alias_overrides:
            return alias_overrides[canonical]
    return DEFAULT_TIME_LIMIT_MULTIPLIERS[canonical]


def scale_time_limit_ms(
    base_time_limit_ms: float,
    language: str,
    manifest: Any | None = None,
) -> float:
    """Scale a base millisecond time limit using ``resolve_time_limit_multiplier``."""
    base_ms = _validate_positive_number(base_time_limit_ms, "base_time_limit_ms")
    return base_ms * resolve_time_limit_multiplier(language, manifest)


def scale_timeout_sec(
    base_timeout_sec: float,
    language: str,
    manifest: Any | None = None,
) -> float:
    """Scale a base timeout in seconds using ``resolve_time_limit_multiplier``."""
    base_sec = _validate_positive_number(base_timeout_sec, "base_timeout_sec")
    return base_sec * resolve_time_limit_multiplier(language, manifest)


def _find_executable(candidates: Sequence[str]) -> str | None:
    for cand in candidates:
        cleaned = _validate_safe_executable_spec(cand, "runtime_executable")
        if os.path.isabs(cleaned) or "/" in cleaned or "\\" in cleaned:
            if os.path.isfile(cleaned) and (os.name == "nt" or os.access(cleaned, os.X_OK)):
                return os.path.abspath(cleaned)
        else:
            found = shutil.which(cleaned)
            if found:
                return found
    return None


def resolve_runtime_executable(
    language: str,
    compiler: str | None = None,
) -> str | None:
    """Resolve the compiler or interpreter executable for ``language``."""
    canonical = normalize_language(language)
    if compiler is not None and compiler.strip():
        cleaned = _validate_safe_executable_spec(compiler, "compiler")
        return _find_executable((cleaned,))
    if canonical == "cpp":
        return _find_executable(("g++", "clang++", "c++"))
    if canonical == "python":
        if sys.executable and os.path.isfile(sys.executable):
            return sys.executable
        return _find_executable(("python3", "python"))
    if canonical == "pypy":
        return _find_executable(("pypy3", "pypy"))
    if canonical == "rust":
        return _find_executable(("rustc",))
    if canonical == "java":
        return _find_executable(("java",))
    return None


def _detect_java_main_class(source_path: str) -> str:
    try:
        content = Path(source_path).read_text(encoding="utf-8", errors="replace")
        match = _JAVA_PUBLIC_CLASS_RE.search(content)
        if match:
            candidate = match.group(1)
            if _JAVA_CLASS_NAME_RE.fullmatch(candidate):
                return candidate
    except OSError:
        pass
    stem = Path(source_path).stem
    if _JAVA_CLASS_NAME_RE.fullmatch(stem):
        return stem
    return "Main"


async def _check_python_syntax(
    abs_src: str,
    interpreter: str,
    *,
    timeout: int | float,
) -> CompileResult:
    """Run ``py_compile`` inside an isolated temporary directory (zero problem_dir writes)."""
    with tempfile.TemporaryDirectory(prefix="autocode_pycompile_") as tmp_dir:
        tmp_pyc = os.path.join(tmp_dir, "checked.pyc")
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPYCACHEPREFIX"] = tmp_dir
        env.setdefault("PYTHONIOENCODING", "utf-8")
        cmd = [
            interpreter,
            "-B",
            "-c",
            "import py_compile, sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)",
            abs_src,
            tmp_pyc,
        ]
        res = await _run_process(
            cmd,
            stdin="",
            timeout=timeout,
            memory_mb=512,
            env=env,
            disable_as_limit=True,
        )
        if res.timed_out:
            return CompileResult(
                success=False,
                error=f"Python syntax check timed out after {timeout}s",
                stdout=res.stdout,
                stderr=res.stderr,
            )
        if not res.success:
            err_detail = (res.stderr or res.error or "Python syntax check failed").strip()
            return CompileResult(
                success=False,
                error=err_detail,
                stdout=res.stdout,
                stderr=res.stderr,
            )
        return CompileResult(
            success=True,
            binary_path=abs_src,
            stdout=res.stdout,
            stderr=res.stderr,
        )


async def _compile_rust(
    abs_src: str,
    abs_bin: str,
    rustc_exe: str,
    *,
    timeout: int | float,
    opt_level: str = "O2",
) -> CompileResult:
    out_dir = os.path.dirname(abs_bin)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    opt_flag = "-O" if opt_level.upper() in {"O2", "O3"} else f"-Copt-level={opt_level.lstrip('O') or '0'}"
    cmd = [rustc_exe, "--edition", "2021", opt_flag, abs_src, "-o", abs_bin]
    res = await _run_process(
        cmd,
        stdin="",
        timeout=timeout,
        memory_mb=1024,
        disable_as_limit=True,
    )
    if res.timed_out:
        return CompileResult(
            success=False,
            error=f"Rust compilation timed out after {timeout}s",
            stdout=res.stdout,
            stderr=res.stderr,
        )
    if not res.success or not os.path.isfile(abs_bin):
        return CompileResult(
            success=False,
            error=res.error or f"Rust compilation failed with code {res.return_code}",
            stdout=res.stdout,
            stderr=res.stderr,
        )
    return CompileResult(
        success=True,
        binary_path=abs_bin,
        stdout=res.stdout,
        stderr=res.stderr,
    )


async def _compile_java(
    abs_src: str,
    class_dir: str,
    javac_exe: str,
    *,
    timeout: int | float,
) -> CompileResult:
    os.makedirs(class_dir, exist_ok=True)
    cmd = [javac_exe, "-encoding", "UTF-8", "-d", class_dir, abs_src]
    res = await _run_process(
        cmd,
        stdin="",
        timeout=timeout,
        memory_mb=1024,
        disable_as_limit=True,
    )
    if res.timed_out:
        return CompileResult(
            success=False,
            error=f"Java compilation timed out after {timeout}s",
            stdout=res.stdout,
            stderr=res.stderr,
        )
    if not res.success:
        return CompileResult(
            success=False,
            error=res.error or f"Java compilation failed with code {res.return_code}",
            stdout=res.stdout,
            stderr=res.stderr,
        )
    return CompileResult(
        success=True,
        binary_path=class_dir,
        stdout=res.stdout,
        stderr=res.stderr,
    )


async def compile_or_prepare(
    source_path: str,
    binary_path: str | None = None,
    *,
    language: str | None = None,
    timeout: int = 30,
    compiler: str | None = None,
    std: str = "c++20",
    opt_level: str = "O2",
    include_dirs: list[str] | None = None,
    extra_sources: list[str] | None = None,
    use_cache: bool = True,
    sanitizers: bool = False,
    extra_flags: list[str] | None = None,
) -> CompileResult:
    """Compile or syntax-check a source file for any supported language.

    For interpreted Python/PyPy sources, ``py_compile`` executes inside an isolated
    ``TemporaryDirectory`` without writing ``__pycache__`` into the source directory
    and returns ``binary_path=os.path.abspath(source_path)``.
    """
    raw_src = _validate_safe_path_str(source_path, "source_path")
    abs_src = os.path.abspath(raw_src)
    if not os.path.isfile(abs_src):
        return CompileResult(success=False, error=f"Source file not found: {source_path}")

    if language is not None:
        eff_lang = normalize_language(language)
    else:
        inferred = infer_language_from_path(abs_src)
        eff_lang = inferred if inferred is not None else "cpp"

    if eff_lang == "cpp":
        target_bin = binary_path
        if not target_bin:
            target_bin = f"{os.path.splitext(abs_src)[0]}{get_exe_extension()}"
        return await compile_cpp(
            abs_src,
            target_bin,
            timeout=timeout,
            compiler=compiler or "g++",
            std=std,
            opt_level=opt_level,
            include_dirs=include_dirs,
            extra_sources=extra_sources,
            use_cache=use_cache,
            sanitizers=sanitizers,
            extra_flags=extra_flags,
        )

    if eff_lang in {"python", "pypy"}:
        interp = resolve_runtime_executable(eff_lang, compiler)
        if interp is None:
            expected_cmd = "python3" if eff_lang == "python" else "pypy3"
            return CompileResult(
                success=False,
                error=f"{eff_lang} interpreter not found (expected {expected_cmd!r} on PATH)",
            )
        return await _check_python_syntax(abs_src, interp, timeout=timeout)

    if eff_lang == "rust":
        rustc_exe = resolve_runtime_executable("rust", compiler)
        if rustc_exe is None:
            return CompileResult(
                success=False,
                error="Rust compiler not found (expected 'rustc' on PATH)",
            )
        target_bin = binary_path or f"{os.path.splitext(abs_src)[0]}{get_exe_extension()}"
        return await _compile_rust(
            abs_src,
            os.path.abspath(_validate_safe_path_str(target_bin, "binary_path")),
            rustc_exe,
            timeout=timeout,
            opt_level=opt_level,
        )

    # eff_lang == "java"
    javac_exe = _find_executable((compiler,) if compiler else ("javac",))
    if javac_exe is None:
        return CompileResult(
            success=False,
            error="Java compiler not found (expected 'javac' on PATH)",
        )
    class_dir = binary_path or f"{os.path.splitext(abs_src)[0]}_classes"
    return await _compile_java(
        abs_src,
        os.path.abspath(_validate_safe_path_str(class_dir, "binary_path")),
        javac_exe,
        timeout=timeout,
    )


async def run_source(
    target_path: str,
    stdin: str = "",
    *,
    language: str | None = None,
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    manifest: Any | None = None,
    apply_multiplier: bool = False,
    args: Sequence[str] = (),
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    runtime_executable: str | None = None,
    main_class: str | None = None,
    process_start_hook: Callable[[int], None] | None = None,
) -> RunResult:
    """Execute a compiled binary or interpreted script with sandbox and resource enforcement."""
    raw_target = _validate_safe_path_str(target_path, "target_path")
    abs_target = os.path.abspath(raw_target)
    if not os.path.exists(abs_target):
        return RunResult(success=False, error=f"Target not found: {target_path}")

    timeout_val = _validate_positive_number(timeout, "timeout")
    memory_val = _validate_positive_number(memory_mb, "memory_mb")

    if language is not None:
        eff_lang = normalize_language(language)
    else:
        inferred = infer_language_from_path(abs_target)
        eff_lang = inferred if inferred is not None else "cpp"

    effective_timeout = (
        scale_timeout_sec(timeout_val, eff_lang, manifest)
        if apply_multiplier
        else timeout_val
    )

    if main_class is not None and not _JAVA_CLASS_NAME_RE.fullmatch(main_class):
        return RunResult(success=False, error=f"Invalid Java main_class: {main_class!r}")

    abs_cwd = os.path.abspath(_validate_safe_path_str(cwd, "cwd")) if cwd is not None else None
    rw_paths: tuple[str, ...] = (abs_cwd,) if abs_cwd is not None else ()
    executor = get_active_sandbox_executor(manifest)

    temp_ctx: tempfile.TemporaryDirectory[str] | None = None
    try:
        base_env: dict[str, str] | None = dict(env) if env is not None else None
        cmd: list[str]
        ro_target_for_mounts: str = abs_target
        extra_ro_dirs: list[str | None] = []

        if eff_lang in {"python", "pypy"}:
            interp = resolve_runtime_executable(eff_lang, runtime_executable)
            if interp is None:
                expected_cmd = "python3" if eff_lang == "python" else "pypy3"
                return RunResult(
                    success=False,
                    error=f"{eff_lang} interpreter not found (expected {expected_cmd!r} on PATH)",
                )
            temp_ctx = tempfile.TemporaryDirectory(prefix="autocode_pyrun_")
            py_env = dict(base_env) if base_env is not None else {}
            py_env["PYTHONDONTWRITEBYTECODE"] = "1"
            py_env["PYTHONPYCACHEPREFIX"] = temp_ctx.name
            py_env.setdefault("PYTHONIOENCODING", "utf-8")
            base_env = py_env
            cmd = [interp, "-B", abs_target, *args]
            if os.path.sep in interp or (os.altsep and os.altsep in interp):
                extra_ro_dirs.append(os.path.dirname(os.path.abspath(interp)))
        elif eff_lang == "java":
            java_exe = resolve_runtime_executable("java", runtime_executable)
            if java_exe is None:
                return RunResult(
                    success=False,
                    error="Java runtime not found (expected 'java' on PATH)",
                )
            if os.path.isdir(abs_target):
                class_dir = abs_target
                eff_main_class = main_class or "Main"
            elif abs_target.lower().endswith(".java"):
                javac_exe = _find_executable(("javac",))
                if javac_exe is None:
                    return RunResult(
                        success=False,
                        error="Java compiler not found (expected 'javac' on PATH)",
                    )
                temp_ctx = tempfile.TemporaryDirectory(prefix="autocode_javac_")
                class_dir = temp_ctx.name
                comp = await _compile_java(abs_target, class_dir, javac_exe, timeout=30)
                if not comp.success:
                    return RunResult(
                        success=False,
                        error=comp.error,
                        stdout=comp.stdout,
                        stderr=comp.stderr,
                    )
                eff_main_class = main_class or _detect_java_main_class(abs_target)
            else:
                class_dir = os.path.dirname(abs_target)
                eff_main_class = main_class or Path(abs_target).stem
            heap_mb = max(16, int(round(memory_val)))
            cmd = [java_exe, f"-Xmx{heap_mb}m", "-cp", class_dir, eff_main_class, *args]
            ro_target_for_mounts = os.path.join(class_dir, f"{eff_main_class}.class")
            extra_ro_dirs.append(class_dir)
        else:
            cmd = [abs_target, *args]

        ro_paths = build_runner_ro_paths(
            ro_target_for_mounts,
            *extra_ro_dirs,
            exclude_dirs=rw_paths,
        )
        exec_cmd, exec_cwd, exec_env = resolve_wrapped_invocation(
            executor,
            cmd,
            cwd=abs_cwd,
            ro_paths=ro_paths,
            rw_paths=rw_paths,
            allow_network=False,
            memory_limit_mb=memory_val,
            time_limit_ms=effective_timeout * 1000.0,
            base_env=base_env,
        )

        # Avoid Linux prlimit --as breaking JVM/PyPy virtual reservations or sandbox CLIs.
        disable_as = executor.name != "none" or eff_lang in {"java", "pypy"}
        result = await _run_process(
            exec_cmd,
            stdin=stdin,
            timeout=effective_timeout,
            memory_mb=memory_val,
            process_start_hook=process_start_hook,
            env=exec_env,
            disable_as_limit=disable_as,
            cwd=exec_cwd,
        )
        if (
            disable_as
            and not result.memory_limit_exceeded
            and result.memory_mb is not None
            and result.memory_mb > memory_val
        ):
            result.memory_limit_exceeded = True
            result.success = False
        return result
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


__all__ = [
    "DEFAULT_TIME_LIMIT_MULTIPLIERS",
    "PreparedTarget",
    "SUPPORTED_LANGUAGES",
    "SupportedLanguage",
    "compile_or_prepare",
    "infer_language_from_path",
    "normalize_language",
    "resolve_runtime_executable",
    "resolve_time_limit_multiplier",
    "run_source",
    "scale_time_limit_ms",
    "scale_timeout_sec",
]
