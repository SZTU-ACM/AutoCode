"""Claude hook compatibility exports.

The authoritative gate table lives in ``autocode_mcp.workflow.enforcement``.
This module keeps the historical hook/test API stable while adapting the
server-oriented gate entries to the old ``(predicate, reason)`` shape.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from autocode_mcp.workflow.enforcement import (  # noqa: E402
    DEFAULT_QUALITY_GATES,
    _min_limit_ratio_gate_ok,
    _validator_gate_ok,
)
from autocode_mcp.workflow.enforcement import (  # noqa: E402
    PRE_GATES as _CORE_PRE_GATES,
)
from autocode_mcp.workflow.manifest import (  # noqa: E402
    AutoCodeManifest,
    manifest_uses_testlib_checker,
)

PRE_GATES = {
    name: [(predicate, reason) for _, (predicate, reason) in gates]
    for name, gates in _CORE_PRE_GATES.items()
}


def _manifest_uses_testlib_checker(manifest: dict[str, Any] | None) -> bool:
    if not isinstance(manifest, dict):
        return False
    try:
        return bool(manifest_uses_testlib_checker(AutoCodeManifest.model_validate(manifest)))
    except (TypeError, ValueError):
        return False


def _append_history(
    state: dict[str, Any],
    *,
    tool: str,
    success: bool,
    key_metrics: dict[str, Any] | None = None,
    gate_result: str = "n/a",
) -> None:
    history = state.get("history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "tool": tool,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gate_result": gate_result,
            "key_metrics": key_metrics or {},
        }
    )
    state["history"] = history[-200:]


__all__ = [
    "DEFAULT_QUALITY_GATES",
    "PRE_GATES",
    "_append_history",
    "_manifest_uses_testlib_checker",
    "_min_limit_ratio_gate_ok",
    "_validator_gate_ok",
]
