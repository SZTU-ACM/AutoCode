"""
AutoCode MCP 工具模块。
"""
from .base import Tool, ToolResult
from .checker import CheckerBuildTool
from .complexity import SolutionAnalyzeTool
from .file_ops import FileReadTool, FileSaveTool
from .generator import GeneratorBuildTool, GeneratorRunTool
from .interactor import InteractorBuildTool
from .problem import (
    ProblemCleanupProcessesTool,
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
)
from .solution import SolutionBuildTool, SolutionRunTool
from .solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from .stress_test import StressTestRunTool
from .test_verify import ProblemVerifyTestsTool
from .validation import ProblemValidateTool
from .validator import ValidatorBuildTool, ValidatorSelectTool

__all__ = [
    "Tool",
    "ToolResult",
    "FileReadTool",
    "FileSaveTool",
    "SolutionBuildTool",
    "SolutionRunTool",
    "SolutionAuditStdTool",
    "SolutionAuditBruteTool",
    "SolutionAnalyzeTool",
    "StressTestRunTool",
    "ProblemCreateTool",
    "ProblemGenerateTestsTool",
    "ProblemCleanupProcessesTool",
    "ProblemVerifyTestsTool",
    "ProblemPackPolygonTool",
    "ProblemValidateTool",
    "ValidatorBuildTool",
    "ValidatorSelectTool",
    "GeneratorBuildTool",
    "GeneratorRunTool",
    "CheckerBuildTool",
    "InteractorBuildTool",
]
