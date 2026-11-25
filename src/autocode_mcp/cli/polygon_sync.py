"""Credential-safe Codeforces Polygon REST API sync CLI (pull & push)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import stat
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..runtime_store import WORKFLOW, get_section
from ..tools.audit import ProblemAuditTool
from ..utils.answer_ext import normalize_answer_ext
from ..utils.communication_runner import is_safe_stem, safe_resolve_under_root
from ..workflow import (
    AutoCodeManifest,
    default_manifest,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    save_manifest,
)
from ..workflow.models import SolutionEntry

DEFAULT_POLYGON_API_URL = "https://polygon.codeforces.com/api"
_RAND_ALPHABET = string.ascii_lowercase + string.digits
_SAFE_METHOD_RE = re.compile(r"^[A-Za-z0-9_.]{1,128}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_MAX_ZIP_EXTRACT_BYTES = 128 * 1024 * 1024

PolygonTransport = Callable[[str, dict[str, str], float], bytes]


class PolygonSyncError(RuntimeError):
    """Base error for Polygon sync operations."""


class PolygonAuthError(PolygonSyncError):
    """Raised when required Polygon API credentials are missing or invalid."""


class PolygonAPIError(PolygonSyncError):
    """Raised when a Polygon API request fails."""


class PolygonPathError(ValueError):
    """Raised when a path fails containment validation."""


class PolygonPrecheckError(PolygonSyncError):
    """Raised when push pre-check fails before any upload occurs."""


def resolve_polygon_credentials(
    api_key: str | None = None,
    api_secret: str | None = None,
) -> tuple[str, str]:
    """Resolve Polygon API key and secret from kwargs or environment variables only."""
    resolved_key = (api_key if api_key is not None else os.environ.get("POLYGON_API_KEY", "")).strip()
    resolved_secret = (
        api_secret if api_secret is not None else os.environ.get("POLYGON_API_SECRET", "")
    ).strip()
    if not resolved_key or not resolved_secret:
        raise PolygonAuthError(
            "Missing Polygon credentials: set POLYGON_API_KEY and POLYGON_API_SECRET environment variables."
        )
    return resolved_key, resolved_secret


def _redact_secrets(text: str, *secret_values: str) -> str:
    cleaned = text
    for val in secret_values:
        if val:
            cleaned = cleaned.replace(val, "[REDACTED]")
    cleaned = re.sub(
        r"(api(?:Key|Secret|Sig)=)[^&\s'\"]+",
        r"\1[REDACTED]",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def compute_polygon_api_sig(
    method_name: str,
    params: Mapping[str, str | int],
    api_secret: str,
    *,
    rand: str | None = None,
) -> str:
    """Compute canonical Polygon ``apiSig`` (``rand + sha512(rand + '/' + method + '?' + sortedParams + '#' + secret)``)."""
    clean_method = method_name.strip().lstrip("/")
    if not _SAFE_METHOD_RE.fullmatch(clean_method):
        raise ValueError(f"Invalid Polygon API method name: {method_name!r}")
    if not api_secret:
        raise PolygonAuthError("Polygon api_secret must be non-empty")

    if rand is None:
        salt = "".join(secrets.choice(_RAND_ALPHABET) for _ in range(6))
    else:
        if len(rand) != 6 or not rand.isascii() or not rand.isalnum():
            raise ValueError("Polygon apiSig rand prefix must be 6 ASCII alphanumeric characters")
        salt = rand

    pairs = sorted(
        (str(k), str(v))
        for k, v in params.items()
        if str(k) != "apiSig"
    )
    sorted_params = "&".join(f"{k}={v}" for k, v in pairs)
    payload = f"{salt}/{clean_method}?{sorted_params}#{api_secret}"
    digest = hashlib.sha512(payload.encode("utf-8")).hexdigest()
    return f"{salt}{digest}"


def build_signed_params(
    method_name: str,
    params: Mapping[str, str | int] | None,
    *,
    api_key: str,
    api_secret: str,
    timestamp: int | None = None,
    rand: str | None = None,
) -> dict[str, str]:
    """Return request parameter dict including ``apiKey``, ``time``, and ``apiSig``."""
    signed: dict[str, str] = {
        str(k): str(v)
        for k, v in (params or {}).items()
        if str(k) != "apiSig"
    }
    signed.setdefault("apiKey", api_key)
    signed.setdefault("time", str(int(time.time()) if timestamp is None else timestamp))
    signed["apiSig"] = compute_polygon_api_sig(
        method_name,
        signed,
        api_secret,
        rand=rand,
    )
    return signed


def _validate_http_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError(f"Polygon base_url must be an http(s) URL, got {url!r}")
    return cleaned


def _default_http_transport(url: str, form_params: dict[str, str], timeout: float) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError(f"Refusing non-HTTP(S) URL: {url!r}")
    encoded = urllib.parse.urlencode(form_params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme validated above
        data: bytes = resp.read()
        return data


class PolygonClient:
    """Minimal dependency-free Codeforces Polygon REST API client."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = DEFAULT_POLYGON_API_URL,
        timeout: float = 30.0,
        transport: PolygonTransport | None = None,
    ) -> None:
        key, secret = resolve_polygon_credentials(api_key, api_secret)
        self._api_key = key
        self._api_secret = secret
        self.base_url = _validate_http_url(base_url)
        self.timeout = float(timeout)
        self._transport = transport or _default_http_transport

    def __repr__(self) -> str:
        return f"PolygonClient(base_url={self.base_url!r}, timeout={self.timeout!r})"

    def call_method(
        self,
        method_name: str,
        params: Mapping[str, str | int] | None = None,
        *,
        expect_json: bool = True,
        timestamp: int | None = None,
        rand: str | None = None,
    ) -> Any:
        """Sign and invoke a Polygon API method."""
        clean_method = method_name.strip().lstrip("/")
        if not _SAFE_METHOD_RE.fullmatch(clean_method):
            raise ValueError(f"Invalid Polygon API method name: {method_name!r}")

        signed_params = build_signed_params(
            clean_method,
            params,
            api_key=self._api_key,
            api_secret=self._api_secret,
            timestamp=timestamp,
            rand=rand,
        )
        url = f"{self.base_url}/{clean_method}"
        try:
            raw = self._transport(url, signed_params, self.timeout)
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except OSError:
                err_body = ""
            safe_detail = _redact_secrets(
                err_body or str(exc.reason or exc.code),
                self._api_key,
                self._api_secret,
                signed_params.get("apiSig", ""),
            )
            raise PolygonAPIError(
                f"Polygon API {clean_method} HTTP {exc.code}: {safe_detail[:300]}"
            ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            safe_detail = _redact_secrets(
                str(exc),
                self._api_key,
                self._api_secret,
                signed_params.get("apiSig", ""),
            )
            raise PolygonAPIError(
                f"Polygon API {clean_method} request failed: {safe_detail[:300]}"
            ) from exc

        if not expect_json:
            if raw.lstrip().startswith(b"{"):
                try:
                    maybe_json = json.loads(raw.decode("utf-8"))
                    if isinstance(maybe_json, dict) and maybe_json.get("status") == "FAILED":
                        comment = _redact_secrets(
                            str(maybe_json.get("comment") or "FAILED"),
                            self._api_key,
                            self._api_secret,
                            signed_params.get("apiSig", ""),
                        )
                        raise PolygonAPIError(f"Polygon API {clean_method} failed: {comment}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
            return raw

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PolygonAPIError(
                f"Polygon API {clean_method} returned invalid JSON"
            ) from exc

        if not isinstance(payload, dict) or payload.get("status") != "OK":
            comment = (
                str(payload.get("comment") or payload.get("status") or "unknown error")
                if isinstance(payload, dict)
                else "malformed response"
            )
            safe_comment = _redact_secrets(
                comment,
                self._api_key,
                self._api_secret,
                signed_params.get("apiSig", ""),
            )
            raise PolygonAPIError(f"Polygon API {clean_method} failed: {safe_comment}")
        return payload.get("result")


def _parse_safe_rel_parts(rel_path: str, *, allow_autocode: bool = False) -> list[str]:
    if not isinstance(rel_path, str) or not rel_path.strip() or "\x00" in rel_path:
        raise PolygonPathError(f"Unsafe relative path: {rel_path!r}")
    if "\\" in rel_path:
        raise PolygonPathError(f"Backslash is not permitted in relative path: {rel_path!r}")
    normalized = rel_path.strip()
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized) or Path(normalized).is_absolute():
        raise PolygonPathError(f"Absolute path escapes target directory: {rel_path!r}")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PolygonPathError(f"Path contains traversal or empty segment: {rel_path!r}")
    if not allow_autocode and parts[0].lower() == ".autocode":
        raise PolygonPathError(f"Refusing remote write into reserved .autocode directory: {rel_path!r}")
    return parts


def _assert_no_symlink_components(root_dir: str, parts: list[str]) -> None:
    real_root = os.path.realpath(root_dir)
    cur = real_root
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise PolygonPathError(f"Refusing symlink component under target_dir: {cur}")


def _safe_write_under_root(
    root_dir: str,
    rel_path: str,
    content: bytes | str,
    *,
    allow_autocode: bool = False,
) -> str:
    parts = _parse_safe_rel_parts(rel_path, allow_autocode=allow_autocode)
    _assert_no_symlink_components(root_dir, parts)
    resolved = safe_resolve_under_root(root_dir, "/".join(parts))
    if resolved is None:
        raise PolygonPathError(f"Path escapes target_dir: {rel_path!r}")
    if os.path.isdir(resolved):
        raise PolygonPathError(f"Target path is a directory, not a file: {rel_path!r}")

    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
    with open(resolved, "wb") as fp:
        fp.write(raw_bytes)
    return "/".join(parts)


def extract_zip_safely(zip_bytes: bytes, target_dir: str) -> list[str]:
    """Extract a Polygon package archive strictly inside ``target_dir`` without zip-slip or symlinks."""
    extracted: list[str] = []
    total_bytes = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for info in zf.infolist():
            raw_name = info.filename
            if stat.S_ISLNK(info.external_attr >> 16):
                raise PolygonPathError(f"Archive contains disallowed symlink: {raw_name!r}")
            if raw_name.endswith("/"):
                continue
            parts = _parse_safe_rel_parts(raw_name)
            if info.file_size > _MAX_ZIP_EXTRACT_BYTES or total_bytes + info.file_size > _MAX_ZIP_EXTRACT_BYTES:
                raise PolygonPathError("Archive exceeds maximum safe extraction size")
            data = zf.read(info)
            total_bytes += len(data)
            written = _safe_write_under_root(target_dir, "/".join(parts), data)
            extracted.append(written)
    return extracted


def _format_statement_markdown(lang: str, stmt: Mapping[str, Any]) -> str:
    title = str(stmt.get("name") or "").strip()
    legend = str(stmt.get("legend") or "").strip()
    inp = str(stmt.get("input") or "").strip()
    out = str(stmt.get("output") or "").strip()
    notes = str(stmt.get("notes") or "").strip()
    sections: list[str] = []
    if title:
        sections.append(f"# {title}")
    if legend:
        sections.append(legend)
    if inp:
        sections.append(f"## Input\n\n{inp}")
    if out:
        sections.append(f"## Output\n\n{out}")
    if notes:
        sections.append(f"## Notes\n\n{notes}")
    if not sections:
        sections.append(f"# Statement ({lang})")
    return "\n\n".join(sections).rstrip() + "\n"


def _solution_role_from_polygon_tag(tag: str) -> tuple[str, str | None]:
    norm = tag.strip().upper()
    if norm == "MA":
        return "main", "pass"
    if norm == "OK":
        return "reference", "pass"
    return "wrong", "fail"


def _polygon_tag_from_solution(entry: SolutionEntry | None, fname: str) -> str:
    if entry is not None:
        if entry.role == "main":
            return "MA"
        if entry.role == "reference" or (entry.role == "wrong" and entry.expected == "pass"):
            return "OK"
        if entry.expected_verdict == "TLE" or entry.role == "brute":
            return "TL"
        if entry.expected_verdict in ("WA", "MLE", "RE"):
            return entry.expected_verdict
        return "RJ"
    if fname == "sol.cpp":
        return "MA"
    if fname == "brute.cpp" or fname.startswith(("tle_", "tle-")):
        return "TL"
    if fname.startswith(("wa_", "wa-")):
        return "WA"
    if fname.startswith(("mle_", "mle-")):
        return "MLE"
    if fname.startswith(("re_", "re-")):
        return "RE"
    return "RJ"


def pull_problem(
    client: PolygonClient,
    problem_id: int,
    target_dir: str,
    *,
    testset: str = "tests",
    pin: str | None = None,
) -> dict[str, Any]:
    """Pull problem metadata, files, solutions, statements, and tests into ``target_dir``."""
    if isinstance(problem_id, bool) or problem_id < 1:
        raise ValueError(f"problem_id must be a positive integer, got {problem_id!r}")
    if os.path.islink(target_dir):
        raise PolygonPathError(f"target_dir cannot be a symlink: {target_dir}")
    if os.path.exists(target_dir) and not os.path.isdir(target_dir):
        raise PolygonPathError(f"target_dir exists and is not a directory: {target_dir}")

    os.makedirs(target_dir, exist_ok=True)
    real_target = os.path.realpath(target_dir)
    for sub in ("files", "solutions", "statements", "tests"):
        sub_path = os.path.join(real_target, sub)
        if os.path.islink(sub_path):
            raise PolygonPathError(f"target subdirectory cannot be a symlink: {sub_path}")
        os.makedirs(sub_path, exist_ok=True)

    base_params: dict[str, str | int] = {"problemId": problem_id}
    if pin is not None:
        base_params["pin"] = pin

    info_raw = client.call_method("problem.info", base_params)
    info = info_raw if isinstance(info_raw, dict) else {}
    time_limit_ms = int(info.get("timeLimit") or 2000)
    memory_limit_mb = int(info.get("memoryLimit") or 256)
    is_interactive = bool(info.get("interactive", False))

    files_written: list[str] = []

    # 1. Statements
    stmts_raw = client.call_method("problem.statements", base_params)
    problem_name = os.path.basename(real_target) or f"polygon_{problem_id}"
    if isinstance(stmts_raw, dict) and stmts_raw:
        preferred_lang = (
            "english"
            if "english" in stmts_raw
            else ("chinese" if "chinese" in stmts_raw else sorted(stmts_raw)[0])
        )
        preferred_stmt = stmts_raw.get(preferred_lang)
        if isinstance(preferred_stmt, dict):
            stmt_name = str(preferred_stmt.get("name") or "").strip()
            if stmt_name:
                problem_name = stmt_name
            files_written.append(
                _safe_write_under_root(
                    real_target,
                    "statements/README.md",
                    _format_statement_markdown(preferred_lang, preferred_stmt),
                )
            )
        for lang, stmt_data in sorted(stmts_raw.items()):
            if not is_safe_stem(str(lang)) or not isinstance(stmt_data, dict):
                continue
            files_written.append(
                _safe_write_under_root(
                    real_target,
                    f"statements/statement_{lang}.md",
                    _format_statement_markdown(str(lang), stmt_data),
                )
            )

    # 2. Files (resource / source / aux)
    files_raw = client.call_method("problem.files", base_params)
    if isinstance(files_raw, dict):
        type_buckets = (
            ("resource", "resourceFiles"),
            ("source", "sourceFiles"),
            ("aux", "auxFiles"),
        )
        for file_type, key in type_buckets:
            entries = files_raw.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                fname = str(entry.get("name") or "")
                _parse_safe_rel_parts(fname)
                raw_bytes = client.call_method(
                    "problem.viewFile",
                    {**base_params, "type": file_type, "name": fname},
                    expect_json=False,
                )
                files_written.append(
                    _safe_write_under_root(real_target, f"files/{fname}", raw_bytes)
                )

    # 3. Solutions
    sols_raw = client.call_method("problem.solutions", base_params)
    manifest_solutions: list[SolutionEntry] = []
    if isinstance(sols_raw, list):
        for entry in sols_raw:
            if not isinstance(entry, dict):
                continue
            sname = str(entry.get("name") or "")
            _parse_safe_rel_parts(sname)
            raw_bytes = client.call_method(
                "problem.viewSolution",
                {**base_params, "name": sname},
                expect_json=False,
            )
            rel_sol = _safe_write_under_root(real_target, f"solutions/{sname}", raw_bytes)
            files_written.append(rel_sol)
            role, expected = _solution_role_from_polygon_tag(str(entry.get("tag") or "RJ"))
            lang_type: str = "python" if sname.endswith(".py") else "cpp"
            manifest_solutions.append(
                SolutionEntry(
                    name=Path(sname).stem,
                    role="main" if role == "main" else ("reference" if role == "reference" else "wrong"),
                    language="python" if lang_type == "python" else "cpp",
                    path=rel_sol,
                    expected="pass" if expected == "pass" else "fail",
                )
            )

    # 4. Tests
    tests_raw = client.call_method("problem.tests", {**base_params, "testset": testset})
    pulled_test_count = 0
    if isinstance(tests_raw, list):
        for pos, test_item in enumerate(tests_raw, 1):
            if not isinstance(test_item, dict):
                continue
            raw_idx = test_item.get("index", pos)
            if isinstance(raw_idx, bool) or not isinstance(raw_idx, int) or raw_idx < 1:
                raise ValueError(f"Invalid Polygon test index: {raw_idx!r}")
            idx = raw_idx
            input_data = test_item.get("input")
            if isinstance(input_data, str):
                in_bytes = input_data.encode("utf-8")
            else:
                in_bytes = client.call_method(
                    "problem.testInput",
                    {**base_params, "testset": testset, "testIndex": idx},
                    expect_json=False,
                )
            files_written.append(
                _safe_write_under_root(real_target, f"tests/{idx:02d}.in", in_bytes)
            )

            ans_data = test_item.get("answer")
            if isinstance(ans_data, str):
                files_written.append(
                    _safe_write_under_root(real_target, f"tests/{idx:02d}.ans", ans_data)
                )
            elif not is_interactive:
                try:
                    ans_bytes = client.call_method(
                        "problem.testAnswer",
                        {**base_params, "testset": testset, "testIndex": idx},
                        expect_json=False,
                    )
                    files_written.append(
                        _safe_write_under_root(real_target, f"tests/{idx:02d}.ans", ans_bytes)
                    )
                except PolygonAPIError:
                    pass
            pulled_test_count += 1

    manifest = default_manifest(
        problem_name,
        interactive=is_interactive,
        problem_type="interactive" if is_interactive else "standard",
        time_limit_ms=max(1, time_limit_ms),
        memory_limit_mb=max(1, memory_limit_mb),
    )
    if manifest_solutions:
        manifest.solutions = manifest_solutions
    save_manifest(real_target, manifest)

    return {
        "action": "pull",
        "problem_id": problem_id,
        "target_dir": real_target,
        "problem_name": problem_name,
        "time_limit_ms": manifest.time_limit_ms,
        "memory_limit_mb": manifest.memory_limit_mb,
        "test_count": pulled_test_count,
        "files_written": files_written,
    }


def _collect_safe_dir_files(problem_dir: str, subdir: str) -> list[tuple[str, str]]:
    """Return sorted ``(rel_path, abs_path)`` files strictly inside ``problem_dir/<subdir>``."""
    sub_root = safe_resolve_under_root(problem_dir, subdir)
    if sub_root is None:
        if os.path.islink(os.path.join(problem_dir, subdir)):
            raise PolygonPathError(f"Subdirectory symlink escapes problem_dir: {subdir}")
        return []
    if not os.path.isdir(sub_root):
        return []
    collected: list[tuple[str, str]] = []
    for entry in sorted(os.listdir(sub_root)):
        rel_path = f"{subdir}/{entry}"
        raw_entry = os.path.join(sub_root, entry)
        if os.path.islink(raw_entry):
            resolved = safe_resolve_under_root(problem_dir, rel_path)
            if resolved is None:
                raise PolygonPathError(f"File symlink escapes problem_dir: {rel_path}")
        else:
            resolved = safe_resolve_under_root(problem_dir, rel_path)
            if resolved is None:
                raise PolygonPathError(f"File escapes problem_dir: {rel_path}")
        if os.path.isfile(resolved):
            collected.append((rel_path, resolved))
    return collected


def run_push_precheck(
    problem_dir: str,
    *,
    allow_unverified: bool = False,
    run_audit: bool = False,
) -> dict[str, Any]:
    """Validate manifest, limits, statement, solutions, tests, and path containment before push."""
    if not os.path.isdir(problem_dir):
        raise PolygonPrecheckError(f"Problem directory not found: {problem_dir}")

    try:
        manifest_model = load_manifest(problem_dir)
    except (ValidationError, OSError, ValueError) as exc:
        raise PolygonPrecheckError(f"Invalid manifest.json: {exc}") from exc

    if manifest_model is None:
        manifest_model = default_manifest(os.path.basename(os.path.normpath(problem_dir)))

    if manifest_model.time_limit_ms <= 0 or manifest_model.memory_limit_mb <= 0:
        raise PolygonPrecheckError("Problem limits (time_limit_ms, memory_limit_mb) must be positive")

    is_interactive = manifest_is_interactive(manifest_model)
    is_communication = manifest_is_communication(manifest_model)
    answers_optional = is_interactive or is_communication

    # Statement check
    stmt_rel: str | None = None
    for cand in (manifest_model.statement_path, "statements/README.md", "README.md"):
        resolved_stmt = safe_resolve_under_root(problem_dir, cand)
        if resolved_stmt is not None and os.path.isfile(resolved_stmt):
            stmt_rel = cand
            break
    if stmt_rel is None:
        raise PolygonPrecheckError("Missing problem statement (statements/README.md)")

    # Main solution check
    sol_files = _collect_safe_dir_files(problem_dir, "solutions")
    has_main = any(rel == "solutions/sol.cpp" for rel, _ in sol_files)
    if not has_main:
        for sol_entry in manifest_model.solutions:
            if sol_entry.role != "main":
                continue
            resolved_main = safe_resolve_under_root(problem_dir, sol_entry.path)
            if resolved_main is not None and os.path.isfile(resolved_main):
                norm_sol_rel = sol_entry.path.replace("\\", "/").lstrip("./")
                if all(rel != norm_sol_rel for rel, _ in sol_files):
                    sol_files.append((norm_sol_rel, resolved_main))
                has_main = True
                break
    if (
        not has_main
        and is_communication
        and manifest_model.protocol is not None
        and manifest_model.protocol.participant_solutions
    ):
        part_paths = [
            safe_resolve_under_root(problem_dir, f"solutions/{name}.cpp")
            for name in manifest_model.protocol.participant_solutions
            if is_safe_stem(name)
        ]
        if (
            len(part_paths) == len(manifest_model.protocol.participant_solutions)
            and all(p is not None and os.path.isfile(p) for p in part_paths)
        ):
            has_main = True
    if not has_main:
        root_sol = safe_resolve_under_root(problem_dir, "sol.cpp")
        if root_sol is not None and os.path.isfile(root_sol):
            sol_files.append(("sol.cpp", root_sol))
            has_main = True
    if not has_main:
        raise PolygonPrecheckError("Missing main solution (solutions/sol.cpp)")

    # Tests check
    tests_root = safe_resolve_under_root(problem_dir, "tests")
    if tests_root is None or not os.path.isdir(tests_root):
        raise PolygonPrecheckError("Missing tests/ directory")

    def _test_sort_key(filename: str) -> tuple[int, int, str]:
        stem = os.path.splitext(filename)[0]
        return (0, int(stem), filename) if stem.isdigit() else (1, 0, filename)

    in_names = sorted(
        (f for f in os.listdir(tests_root) if f.endswith(".in")),
        key=_test_sort_key,
    )
    if not in_names:
        raise PolygonPrecheckError("No .in test files found under tests/")

    answer_ext = normalize_answer_ext(".ans") or ".ans"
    test_pairs: list[tuple[int, str, str | None]] = []
    for idx, in_name in enumerate(in_names, 1):
        in_abs = safe_resolve_under_root(problem_dir, f"tests/{in_name}")
        if in_abs is None or not os.path.isfile(in_abs):
            raise PolygonPrecheckError(f"Test input escapes problem_dir or missing: tests/{in_name}")
        if os.path.getsize(in_abs) == 0:
            raise PolygonPrecheckError(f"Empty test input file: tests/{in_name}")
        ans_name = f"{os.path.splitext(in_name)[0]}{answer_ext}"
        ans_abs = safe_resolve_under_root(problem_dir, f"tests/{ans_name}")
        if ans_abs is None:
            raise PolygonPrecheckError(f"Test answer escapes problem_dir: tests/{ans_name}")
        if not os.path.isfile(ans_abs):
            if not answers_optional:
                raise PolygonPrecheckError(f"Missing answer file for test: tests/{ans_name}")
            test_pairs.append((idx, in_abs, None))
        else:
            test_pairs.append((idx, in_abs, ans_abs))

    resource_and_source_files = _collect_safe_dir_files(problem_dir, "files")

    # Optional workflow verification gate check when runtime.json exists
    runtime_file = os.path.join(problem_dir, ".autocode", "runtime.json")
    if (
        os.path.isfile(runtime_file)
        and manifest_model.quality_gates.require_tests_verified
        and not allow_unverified
    ):
        workflow_state = get_section(problem_dir, WORKFLOW)
        if not isinstance(workflow_state, dict) or not bool(workflow_state.get("tests_verified", False)):
            raise PolygonPrecheckError(
                "Tests are not verified in .autocode/runtime.json; run verification first or pass --allow-unverified"
            )

    if run_audit:
        audit_res = asyncio.run(
            ProblemAuditTool().execute(problem_dir=problem_dir, mode="quick", standalone=True)
        )
        if not audit_res.success or audit_res.data.get("decision") != "go":
            raise PolygonPrecheckError(
                f"Standalone quick audit failed pre-check: {audit_res.error or audit_res.data.get('decision')}"
            )

    return {
        "manifest": manifest_model,
        "statement_rel": stmt_rel,
        "files": resource_and_source_files,
        "solutions": sol_files,
        "test_pairs": test_pairs,
        "is_interactive": is_interactive,
    }


def push_problem(
    client: PolygonClient,
    problem_id: int,
    problem_dir: str,
    *,
    testset: str = "tests",
    allow_unverified: bool = False,
    run_audit: bool = False,
    pin: str | None = None,
) -> dict[str, Any]:
    """Run pre-check and upload problem limits, files, solutions, tests, and statement to Polygon."""
    if isinstance(problem_id, bool) or problem_id < 1:
        raise ValueError(f"problem_id must be a positive integer, got {problem_id!r}")

    checked = run_push_precheck(
        problem_dir,
        allow_unverified=allow_unverified,
        run_audit=run_audit,
    )
    manifest: AutoCodeManifest = checked["manifest"]
    is_interactive: bool = bool(checked["is_interactive"])

    base_params: dict[str, str | int] = {"problemId": problem_id}
    if pin is not None:
        base_params["pin"] = pin

    # 1. Limits & info
    client.call_method(
        "problem.updateInfo",
        {
            **base_params,
            "inputFile": "stdin",
            "outputFile": "stdout",
            "interactive": "true" if is_interactive else "false",
            "timeLimit": manifest.time_limit_ms,
            "memoryLimit": manifest.memory_limit_mb,
        },
    )

    # 2. Files (resources & sources) + set validator/checker/interactor
    uploaded_files: list[str] = []
    for rel_path, abs_path in checked["files"]:
        fname = os.path.basename(rel_path)
        file_type = "resource" if fname.endswith((".h", ".hpp")) else "source"
        content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        client.call_method(
            "problem.saveFile",
            {**base_params, "type": file_type, "name": fname, "file": content},
        )
        uploaded_files.append(fname)
        if fname == "val.cpp":
            client.call_method("problem.setValidator", {**base_params, "validator": fname})
        elif fname == "checker.cpp":
            client.call_method("problem.setChecker", {**base_params, "checker": fname})
        elif fname == "interactor.cpp":
            client.call_method("problem.setInteractor", {**base_params, "interactor": fname})

    # 3. Solutions
    manifest_sol_map = {
        s.path.replace("\\", "/").lstrip("./"): s
        for s in manifest.solutions
    }
    uploaded_solutions: list[str] = []
    for rel_path, abs_path in checked["solutions"]:
        fname = os.path.basename(rel_path)
        sol_entry = manifest_sol_map.get(rel_path)
        tag = _polygon_tag_from_solution(sol_entry, fname)
        content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        client.call_method(
            "problem.saveSolution",
            {**base_params, "name": fname, "file": content, "tag": tag},
        )
        uploaded_solutions.append(fname)

    # 4. Tests
    uploaded_tests = 0
    for idx, in_abs, _ in checked["test_pairs"]:
        in_text = Path(in_abs).read_text(encoding="utf-8", errors="replace")
        client.call_method(
            "problem.saveTest",
            {
                **base_params,
                "testset": testset,
                "testIndex": idx,
                "testInput": in_text,
                "checkExisting": "false",
            },
        )
        uploaded_tests += 1

    # 5. Statement
    stmt_rel = checked["statement_rel"]
    stmt_abs = safe_resolve_under_root(problem_dir, stmt_rel)
    if stmt_abs is not None and os.path.isfile(stmt_abs):
        stmt_text = Path(stmt_abs).read_text(encoding="utf-8", errors="replace")
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in stmt_text)
        client.call_method(
            "problem.saveStatement",
            {
                **base_params,
                "lang": "chinese" if has_cjk else "english",
                "encoding": "UTF-8",
                "name": manifest.problem_name,
                "legend": stmt_text,
            },
        )

    return {
        "action": "push",
        "problem_id": problem_id,
        "problem_dir": os.path.abspath(problem_dir),
        "time_limit_ms": manifest.time_limit_ms,
        "memory_limit_mb": manifest.memory_limit_mb,
        "uploaded_files": uploaded_files,
        "uploaded_solutions": uploaded_solutions,
        "uploaded_tests": uploaded_tests,
    }


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``python -m autocode_mcp.cli.polygon_sync``."""
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Sync AutoCode problem packages with Codeforces Polygon REST API (credentials via env vars only)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pull_p = subparsers.add_parser("pull", help="Download a Polygon problem into target_dir")
    pull_p.add_argument("problem_id", type=int, help="Numeric Polygon problem ID")
    pull_p.add_argument("target_dir", help="Local target directory")
    pull_p.add_argument("--testset", default="tests", help="Polygon testset name (default: tests)")
    pull_p.add_argument("--base-url", default=DEFAULT_POLYGON_API_URL, help="Polygon API base URL")
    pull_p.add_argument("--pin", default=None, help="Optional Polygon problem PIN")

    push_p = subparsers.add_parser("push", help="Run pre-check and upload problem_dir to Polygon")
    push_p.add_argument("problem_id", type=int, help="Numeric Polygon problem ID")
    push_p.add_argument("problem_dir", help="Local AutoCode problem directory")
    push_p.add_argument("--testset", default="tests", help="Polygon testset name (default: tests)")
    push_p.add_argument("--base-url", default=DEFAULT_POLYGON_API_URL, help="Polygon API base URL")
    push_p.add_argument("--pin", default=None, help="Optional Polygon problem PIN")
    push_p.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Allow push even if .autocode/runtime.json has tests_verified=False",
    )
    push_p.add_argument(
        "--audit",
        action="store_true",
        help="Run standalone quick audit before uploading",
    )

    args = parser.parse_args(argv)
    try:
        client = PolygonClient(base_url=args.base_url)
        if args.command == "pull":
            result = pull_problem(
                client,
                args.problem_id,
                args.target_dir,
                testset=args.testset,
                pin=args.pin,
            )
        else:
            result = push_problem(
                client,
                args.problem_id,
                args.problem_dir,
                testset=args.testset,
                allow_unverified=args.allow_unverified,
                run_audit=args.audit,
                pin=args.pin,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (PolygonSyncError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
