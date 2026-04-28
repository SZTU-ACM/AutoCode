"""
AutoCode MCP Server - 竞赛编程出题辅助工具

基于论文《AutoCode: LLMs as Problem Setters for Competitive Programming》
实现 Validator-Generator-Checker 框架。
"""
import os

__version__ = "0.8.0"

# 获取 templates 目录路径（包内目录）
_PACKAGE_DIR = os.path.dirname(__file__)
TEMPLATES_DIR = os.path.join(_PACKAGE_DIR, "templates")
