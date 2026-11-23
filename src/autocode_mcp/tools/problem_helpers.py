"""Shared facade helper & constants for split Problem tools."""

from __future__ import annotations

import re
import sys
from typing import TypeVar, cast

# 最终测试集中「极限类」占比下限：至少一半来自 generator type 3/4（extreme + TLE 压力）
_LIMIT_STRATEGY_TYPES = frozenset({"3", "4"})
_GENERATOR_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_T = TypeVar("_T")


def _facade_attr(name: str, default: _T) -> _T:
    """Resolve patchable module attribute through ``autocode_mcp.tools.problem`` facade."""
    mod = sys.modules.get("autocode_mcp.tools.problem")
    return cast(_T, getattr(mod, name, default)) if mod is not None else default
