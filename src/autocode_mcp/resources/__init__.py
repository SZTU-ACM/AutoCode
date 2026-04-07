"""
AutoCode MCP Resources 模块。

提供题目文件、测试数据、配置等资源访问。
"""
from __future__ import annotations

import os

from .. import TEMPLATES_DIR


def get_template_path(name: str) -> str | None:
    """
    获取模板文件路径。

    Args:
        name: 模板名称（如 "testlib.h", "validator_template.cpp"）

    Returns:
        模板文件路径，不存在则返回 None
    """
    path = os.path.join(TEMPLATES_DIR, name)
    if os.path.exists(path):
        return path
    return None


def list_templates() -> list[str]:
    """
    列出所有可用模板。

    Returns:
        模板文件名列表
    """
    if not os.path.exists(TEMPLATES_DIR):
        return []
    return os.listdir(TEMPLATES_DIR)


def get_problem_resource_path(problem_dir: str, resource_type: str) -> str | None:
    """
    获取题目资源路径。

    Args:
        problem_dir: 题目目录
        resource_type: 资源类型（statement, constraints, config 等）

    Returns:
        资源路径，不存在则返回 None
    """
    type_to_path = {
        "statement": os.path.join(problem_dir, "statements", "README.md"),
        "constraints": os.path.join(problem_dir, "constraints.json"),
        "config": os.path.join(problem_dir, "config.json"),
    }

    path = type_to_path.get(resource_type)
    if path and os.path.exists(path):
        return path
    return None
