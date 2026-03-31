"""
平台相关工具函数。

提供跨平台的工具函数，处理不同操作系统的差异。
"""
import sys


def get_exe_extension() -> str:
    """获取当前平台的可执行文件扩展名。

    Returns:
        str: Windows 返回 ".exe"，其他平台返回空字符串
    """
    return ".exe" if sys.platform == "win32" else ""


def is_windows() -> bool:
    """检查当前是否为 Windows 平台。

    Returns:
        bool: Windows 返回 True，其他返回 False
    """
    return sys.platform == "win32"


def is_linux() -> bool:
    """检查当前是否为 Linux 平台。

    Returns:
        bool: Linux 返回 True，其他返回 False
    """
    return sys.platform.startswith("linux")


def is_macos() -> bool:
    """检查当前是否为 macOS 平台。

    Returns:
        bool: macOS 返回 True，其他返回 False
    """
    return sys.platform == "darwin"
