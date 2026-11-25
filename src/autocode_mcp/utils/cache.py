"""
编译缓存模块。

缓存已编译的二进制文件，避免重复编译。

缓存键基于源文件内容与编译参数（内容未变即命中，源变更即失效）。存储按
源文件绝对路径分桶（``cache_dir/<bucket>/<content_key>``），并维护每桶的
mtime/size 索引作为快速路径：当源文件 mtime 与 size 均未变时，直接复用上次
计算出的内容键，避免每次都重新读取整份源文件计算哈希。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

_INDEX_NAME = "index.json"
# 文件系统 mtime 精度可能较粗（FAT 达 2s）。对最近修改的源文件强制走内容哈希，
# 避免"同长度内容修改 + mtime 未变"导致的陈旧命中。
_MTIME_SAFE_WINDOW_SEC = 2.0


def default_compile_cache_dir() -> Path:
    """解析默认用户级编译缓存目录（绝不回退到工作目录 `.cache/`）。

    优先级：
    1. ``AUTOCODE_CACHE_DIR`` 环境变量
    2. ``XDG_CACHE_HOME/autocode-mcp/compile``
    3. OS 用户级缓存目录（Windows ``LOCALAPPDATA`` / macOS ``~/Library/Caches`` / POSIX ``~/.cache``）
    4. ``tempfile.gettempdir()/autocode_mcp_compile_cache``
    """
    env_dir = os.environ.get("AUTOCODE_CACHE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()

    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        return Path(xdg_cache).expanduser() / "autocode-mcp" / "compile"

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data).expanduser() / "autocode-mcp" / "compile"
        try:
            home = Path.home()
            if home != Path(home.anchor):
                return home / "AppData" / "Local" / "autocode-mcp" / "compile"
        except (RuntimeError, OSError):
            pass
    elif sys.platform == "darwin":
        try:
            home = Path.home()
            if home != Path(home.anchor):
                return home / "Library" / "Caches" / "autocode-mcp" / "compile"
        except (RuntimeError, OSError):
            pass
    else:
        try:
            home = Path.home()
            if home != Path(home.anchor):
                return home / ".cache" / "autocode-mcp" / "compile"
        except (RuntimeError, OSError):
            pass

    return Path(tempfile.gettempdir()) / "autocode_mcp_compile_cache"


class CompileCache:
    """编译缓存类。

    缓存已编译的二进制文件，基于源文件内容和编译参数生成缓存键；
    存储按源文件路径分桶，并用 mtime/size 索引加速键计算。
    初始化阶段不做任何目录创建，仅在实际写入缓存时懒创建目录。
    """

    def __init__(self, cache_dir: str | os.PathLike[str] | None = None) -> None:
        """初始化缓存配置（不创建任何目录）。

        Args:
            cache_dir: 可选显式缓存目录路径；为 None 时使用用户级/临时默认缓存目录
        """
        self._explicit_cache_dir: Path | None = None
        if cache_dir is not None:
            raw = os.fspath(cache_dir)
            raw_str = os.fsdecode(raw) if isinstance(raw, bytes) else raw
            if raw_str.strip():
                self._explicit_cache_dir = Path(raw_str)
        self._fallback_cache_dir: Path | None = None
        self._disabled = False

    @property
    def cache_dir(self) -> Path:
        """当前生效的缓存根目录。"""
        if self._fallback_cache_dir is not None:
            return self._fallback_cache_dir
        if self._explicit_cache_dir is not None:
            return self._explicit_cache_dir
        return default_compile_cache_dir()

    @cache_dir.setter
    def cache_dir(self, value: str | os.PathLike[str]) -> None:
        self._explicit_cache_dir = Path(value)
        self._fallback_cache_dir = None

    @staticmethod
    def _platform_tag() -> str:
        return f"{sys.platform}/{(_platform.machine() or 'unknown').lower()}"

    def _bucket(self, source_path: str) -> str:
        """按宿主平台与源文件绝对路径生成分桶目录名（避免跨平台共享缓存冲突）。"""
        abspath = os.path.abspath(source_path)
        return hashlib.sha256(f"{self._platform_tag()}:{abspath}".encode()).hexdigest()[:16]

    def _ensure_bucket_dir(self, source_path: str) -> Path:
        bucket = self._bucket(source_path)
        target_bucket = self.cache_dir / bucket
        try:
            target_bucket.mkdir(parents=True, exist_ok=True)
            return target_bucket
        except OSError:
            if self._explicit_cache_dir is None:
                fallback = Path(tempfile.gettempdir()) / "autocode_mcp_compile_cache"
                fallback_bucket = fallback / bucket
                try:
                    fallback_bucket.mkdir(parents=True, exist_ok=True)
                    self._fallback_cache_dir = fallback
                    return fallback_bucket
                except OSError:
                    self._disabled = True
                    raise
            self._disabled = True
            raise

    def _bucket_dir(self, source_path: str, *, create: bool = True) -> Path:
        if not create:
            return self.cache_dir / self._bucket(source_path)
        return self._ensure_bucket_dir(source_path)

    def _params(
        self,
        compiler: str,
        std: str,
        opt: str,
        extra_flags: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        base = f"{compiler}:{std}:{opt}"
        if extra_flags:
            flags_json = json.dumps([str(f) for f in extra_flags], separators=(",", ":"))
            return f"{base}|{flags_json}"
        return base

    def _content_key(
        self,
        source_path: str,
        compiler: str,
        std: str,
        opt: str,
        extra_flags: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        """基于宿主平台、源文件内容与编译参数生成 16 字符哈希键。"""
        content = Path(source_path).read_bytes()
        digest = hashlib.sha256()
        digest.update(self._platform_tag().encode("utf-8"))
        digest.update(b":")
        digest.update(content)
        digest.update(self._params(compiler, std, opt, extra_flags).encode("utf-8"))
        return digest.hexdigest()[:16]

    def _get_key(
        self,
        source_path: str,
        compiler: str,
        std: str,
        opt: str,
        extra_flags: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        """生成缓存键（内容 + 编译参数）。保持向后兼容的公共判定入口。"""
        return self._content_key(source_path, compiler, std, opt, extra_flags)

    def _stat(self, source_path: str) -> tuple[float, int] | None:
        try:
            st = os.stat(source_path)
        except OSError:
            return None
        return st.st_mtime, st.st_size

    def _read_index(self, bucket_dir: Path) -> dict[str, Any]:
        index_path = bucket_dir / _INDEX_NAME
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_index(self, bucket_dir: Path, index: dict[str, Any]) -> None:
        try:
            (bucket_dir / _INDEX_NAME).write_text(
                json.dumps(index, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _resolve_key(
        self,
        source_path: str,
        compiler: str,
        std: str,
        opt: str,
        extra_flags: tuple[str, ...] | list[str] | None = None,
        *,
        create_bucket: bool = True,
        write_index: bool = True,
    ) -> str:
        """解析缓存键：mtime/size 未变时走快速路径，否则重算内容键并按需刷新索引。"""
        bucket_dir = self._bucket_dir(source_path, create=create_bucket)
        params = self._params(compiler, std, opt, extra_flags)
        stat = self._stat(source_path)
        index = self._read_index(bucket_dir) if bucket_dir.is_dir() else {}
        entry = index.get(params)
        recently_modified = stat is not None and (time.time() - stat[0]) < _MTIME_SAFE_WINDOW_SEC
        if (
            stat is not None
            and not recently_modified
            and isinstance(entry, dict)
            and entry.get("mtime") == stat[0]
            and entry.get("size") == stat[1]
            and isinstance(entry.get("key"), str)
        ):
            return str(entry["key"])
        key = self._content_key(source_path, compiler, std, opt, extra_flags)
        if write_index and stat is not None and bucket_dir.is_dir():
            index[params] = {"mtime": stat[0], "size": stat[1], "key": key}
            self._write_index(bucket_dir, index)
        return key

    def get(
        self,
        source_path: str,
        compiler: str,
        std: str,
        opt: str,
        extra_flags: tuple[str, ...] | list[str] | None = None,
    ) -> str | None:
        """获取缓存的二进制文件路径（缺失分桶时不创建任何目录或文件）。"""
        if self._disabled:
            return None
        try:
            bucket_dir = self._bucket_dir(source_path, create=False)
            if not bucket_dir.is_dir():
                return None
            key = self._resolve_key(
                source_path,
                compiler,
                std,
                opt,
                extra_flags,
                create_bucket=False,
                write_index=False,
            )
            cached_binary = bucket_dir / key
            if cached_binary.is_file():
                return str(cached_binary)
        except OSError:
            return None
        return None

    def set(
        self,
        source_path: str,
        binary_path: str,
        compiler: str,
        std: str,
        opt: str,
        extra_flags: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        """将二进制文件存入缓存（懒创建目录，写入失败则降级返回原路径）。"""
        if self._disabled:
            return binary_path
        tmp_path: Path | None = None
        try:
            bucket_dir = self._bucket_dir(source_path, create=True)
            key = self._resolve_key(
                source_path,
                compiler,
                std,
                opt,
                extra_flags,
                create_bucket=True,
                write_index=True,
            )
            cached_binary = bucket_dir / key
            tmp_path = bucket_dir / f".{key}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
            shutil.copy2(binary_path, tmp_path)
            os.replace(tmp_path, cached_binary)
            tmp_path = None
            return str(cached_binary)
        except OSError:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return binary_path
