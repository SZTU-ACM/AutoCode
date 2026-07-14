"""质量门禁（QualityGate）判定的唯一入口。

`problem_pack_polygon` 与 `problem_audit` 均通过 :func:`check_gates` 判定
验证信号类质量门禁，避免两处各自实现导致行为漂移。调用方仅负责在各自
上下文里把返回的 :class:`GateIssue` 格式化成面向用户的错误信息 / 报告项。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import AutoCodeManifest


def signal_satisfied(signal: object) -> bool:
    """验证信号是否既已执行又已通过。"""
    return isinstance(signal, dict) and bool(signal.get("executed")) and bool(signal.get("passed"))


@dataclass(frozen=True)
class GateIssue:
    """单条阻断门禁项。

    Attributes:
        gate: 门禁标识（如 ``tests_verified`` / ``limit_semantics``）
        reason: 阻断原因（简明、可直接展示）
    """

    gate: str
    reason: str


def check_gates(
    manifest: AutoCodeManifest,
    workflow_state: dict,
    verify_signals: dict,
) -> list[GateIssue]:
    """判定验证信号类质量门禁。

    该函数是 QualityGate 判定的唯一真值来源；返回按 ``problem_pack_polygon``
    评估顺序排列的阻断项列表，空列表表示全部通过。

    Args:
        manifest: 已加载并校验的 :class:`AutoCodeManifest`
        workflow_state: ``.autocode-workflow/state.json`` 的解析结果
        verify_signals: ``workflow_state['verify_signals']``（验证信号字典）

    Returns:
        阻断门禁项列表；空列表表示通过。
    """
    issues: list[GateIssue] = []
    gates = manifest.quality_gates

    if gates.require_tests_verified and not bool(workflow_state.get("tests_verified")):
        issues.append(GateIssue("tests_verified", "run problem_verify_tests first"))

    signal_rules = [
        ("limit_semantics", gates.require_limit_semantics),
        ("wrong_solution_kill", gates.require_wrong_solution_kill),
        ("validator_check", gates.require_validator_check),
    ]
    for name, enabled in signal_rules:
        if not enabled:
            continue
        if not signal_satisfied(verify_signals.get(name, {})):
            issues.append(GateIssue(name, f"verification signal `{name}` not satisfied"))

    return issues
