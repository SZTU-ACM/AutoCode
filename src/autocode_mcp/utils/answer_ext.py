"""答案文件扩展名（answer_ext）归一化的唯一入口。

``problem_generate_tests`` 与 ``problem_verify_tests`` 共用同一套规则，避免两处
各自维护导致行为漂移。规则：去空白、补前导点、拒绝非法字符 / 纯点 / ``.in``。
"""

from __future__ import annotations

# 文件名非法字符（含 & 以规避 shell/XML 歧义）。
_ILLEGAL_CHARS = ("/", "\\", ":", "*", "?", '"', "<", ">", "|", "&")


def normalize_answer_ext(answer_ext: str | None) -> str | None:
    """归一化 answer_ext；非法或空值返回 ``None``。

    Args:
        answer_ext: 原始扩展名（可含或不含前导点，可能为 ``None``）

    Returns:
        归一化后的扩展名（形如 ``.ans``）；非法时返回 ``None``。
    """
    if not isinstance(answer_ext, str):
        return None
    ext = answer_ext.strip()
    if not ext:
        return None
    if not ext.startswith("."):
        ext = f".{ext}"
    # 拒绝纯点（如 "." / ".."）
    if not any(ch != "." for ch in ext[1:]):
        return None
    if any(ch in ext for ch in _ILLEGAL_CHARS):
        return None
    if ext == ".in":
        return None
    return ext
