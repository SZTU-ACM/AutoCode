"""
编译缓存模块。

缓存已编译的二进制文件，避免重复编译。
"""

import hashlib
import shutil
from pathlib import Path


class CompileCache:
    """编译缓存类。

    缓存已编译的二进制文件，基于源文件内容和编译参数生成缓存键。
    """

    def __init__(self, cache_dir: str = ".cache/compile"):
        """初始化缓存。

        Args:
            cache_dir: 缓存目录路径
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_key(self, source_path: str, compiler: str, std: str, opt: str) -> str:
        """生成缓存键。

        基于源文件内容和编译参数生成 16 字符的哈希键。

        Args:
            source_path: 源文件路径
            compiler: 编译器名称
            std: C++ 标准版本
            opt: 优化级别

        Returns:
            str: 16 字符的哈希键
        """
        content = Path(source_path).read_text(encoding="utf-8")
        data = f"{content}:{compiler}:{std}:{opt}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def get(self, source_path: str, compiler: str, std: str, opt: str) -> str | None:
        """获取缓存的二进制文件路径。

        Args:
            source_path: 源文件路径
            compiler: 编译器名称
            std: C++ 标准版本
            opt: 优化级别

        Returns:
            str | None: 缓存的二进制文件路径，不存在则返回 None
        """
        key = self._get_key(source_path, compiler, std, opt)
        cached_binary = self.cache_dir / key

        if cached_binary.exists():
            return str(cached_binary)
        return None

    def set(self, source_path: str, binary_path: str, compiler: str, std: str, opt: str) -> str:
        """将二进制文件存入缓存。

        Args:
            source_path: 源文件路径
            binary_path: 二进制文件路径
            compiler: 编译器名称
            std: C++ 标准版本
            opt: 优化级别

        Returns:
            str: 缓存后的二进制文件路径
        """
        key = self._get_key(source_path, compiler, std, opt)
        cached_binary = self.cache_dir / key

        shutil.copy2(binary_path, cached_binary)

        return str(cached_binary)
