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
from pathlib import Path

_INDEX_NAME = "index.json"
# 文件系统 mtime 精度可能较粗（FAT 达 2s）。对最近修改的源文件强制走内容哈希，
# 避免"同长度内容修改 + mtime 未变"导致的陈旧命中。
_MTIME_SAFE_WINDOW_SEC = 2.0


class CompileCache:
    """编译缓存类。

    缓存已编译的二进制文件，基于源文件内容和编译参数生成缓存键；
    存储按源文件路径分桶，并用 mtime/size 索引加速键计算。
    """

    def __init__(self, cache_dir: str = ".cache/compile"):
        """初始化缓存。

        Args:
            cache_dir: 缓存目录路径
        """
        self.cache_dir = Path(cache_dir)
        self._disabled = False
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            if cache_dir == ".cache/compile":
                fallback = Path(tempfile.gettempdir()) / "autocode_mcp_compile_cache"
                try:
                    fallback.mkdir(parents=True, exist_ok=True)
                    self.cache_dir = fallback
                except OSError:
                    self._disabled = True
            else:
                self._disabled = True

    @staticmethod
    def _platform_tag() -> str:
        return f"{sys.platform}/{(_platform.machine() or 'unknown').lower()}"

    def _bucket(self, source_path: str) -> str:
        """按宿主平台与源文件绝对路径生成分桶目录名（避免跨平台共享缓存冲突）。"""
        abspath = os.path.abspath(source_path)
        return hashlib.sha256(f"{self._platform_tag()}:{abspath}".encode()).hexdigest()[:16]

    def _bucket_dir(self, source_path: str) -> Path:
        path = self.cache_dir / self._bucket(source_path)
        path.mkdir(parents=True, exist_ok=True)
        return path

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

    def _read_index(self, bucket_dir: Path) -> dict:
        index_path = bucket_dir / _INDEX_NAME
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_index(self, bucket_dir: Path, index: dict) -> None:
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
    ) -> str:
        """解析缓存键：mtime/size 未变时走快速路径，否则重算内容键并刷新索引。"""
        bucket_dir = self._bucket_dir(source_path)
        params = self._params(compiler, std, opt, extra_flags)
        stat = self._stat(source_path)
        index = self._read_index(bucket_dir)
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
        if stat is not None:
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
        """获取缓存的二进制文件路径。"""
        if self._disabled:
            return None
        try:
            key = self._resolve_key(source_path, compiler, std, opt, extra_flags)
            cached_binary = self._bucket_dir(source_path) / key
            if cached_binary.exists():
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
        """将二进制文件存入缓存。"""
        if self._disabled:
            return binary_path
        try:
            key = self._resolve_key(source_path, compiler, std, opt, extra_flags)
            cached_binary = self._bucket_dir(source_path) / key
            shutil.copy2(binary_path, cached_binary)
            return str(cached_binary)
        except OSError:
            return binary_path
