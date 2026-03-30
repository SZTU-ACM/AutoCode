"""
AutoCode MCP Server - 竞赛编程出题辅助工具

基于论文《AutoCode: LLMs as Problem Setters for Competitive Programming》
实现 Validator-Generator-Checker 框架。
"""
import os

__version__ = "0.1.0"

# 获取 templates 目录路径
# __file__ = src/autocode_mcp/__init__.py
# templates = autocode_mcp/templates (需要往上两层)
_PACKAGE_DIR = os.path.dirname(__file__)
_SRC_DIR = os.path.dirname(_PACKAGE_DIR)
PROJECT_ROOT = os.path.dirname(_SRC_DIR)
TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "templates")
