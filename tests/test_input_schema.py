"""
Tests for Pydantic-derived tool input schemas (change 7.2 / 7.3).

Verifies that ``input_schema_from_model`` produces schemas whose field names,
types, enums, defaults and required sets match the tool contracts, that nested
objects are inlined (no ``$ref``/``$defs``), and that migrated tools expose the
derived schema as their single source of truth.
"""
import json
import re
from pathlib import Path
from typing import Any

from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.base import input_schema_from_model
from autocode_mcp.tools.build_all import ProblemBuildAllTool
from autocode_mcp.tools.checker import CheckerBuildTool
from autocode_mcp.tools.complexity import SolutionAnalyzeTool
from autocode_mcp.tools.file_ops import FileReadTool, FileSaveTool
from autocode_mcp.tools.generator import GeneratorBuildTool, GeneratorRunTool
from autocode_mcp.tools.interactor import InteractorBuildTool
from autocode_mcp.tools.problem import (
    ProblemCleanupProcessesTool,
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
)
from autocode_mcp.tools.schemas import (
    CheckerBuildInput,
    FileReadInput,
    FileSaveInput,
    GeneratorBuildInput,
    GeneratorRunInput,
    InteractorBuildInput,
    ProblemAuditInput,
    ProblemBuildAllInput,
    ProblemCleanupProcessesInput,
    ProblemCreateInput,
    ProblemGenerateTestsInput,
    ProblemPackPolygonInput,
    ProblemValidateInput,
    ProblemVerifyTestsInput,
    SolutionAnalyzeInput,
    SolutionAuditBruteInput,
    SolutionAuditStdInput,
    SolutionBuildInput,
    SolutionRunInput,
    StatementSample,
    StressGeneratorArgs,
    StressTestRunInput,
    ValidatorBuildInput,
    ValidatorSelectInput,
)
from autocode_mcp.tools.solution import SolutionBuildTool, SolutionRunTool
from autocode_mcp.tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from autocode_mcp.tools.stress_test import StressTestRunTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.tools.validation import ProblemValidateTool
from autocode_mcp.tools.validator import ValidatorBuildTool, ValidatorSelectTool


def _non_null(spec: object) -> dict[str, Any]:
    """Extract the non-null branch of an ``anyOf`` produced for Optional fields."""
    if isinstance(spec, dict) and "anyOf" in spec:
        for sub in spec["anyOf"]:
            if isinstance(sub, dict) and sub.get("type") != "null":
                return sub
    assert isinstance(spec, dict)
    return spec


def test_audit_schema_from_model():
    schema = input_schema_from_model(ProblemAuditInput)
    assert schema["type"] == "object"
    props = schema["properties"]
    assert set(props) == {
        "problem_dir",
        "mode",
        "include_difficulty",
        "report_path",
        "standalone",
        "layout",
        "enable_mutation_testing",
        "strict",
    }
    assert props["problem_dir"]["type"] == "string"
    assert props["mode"]["enum"] == ["quick", "full"]
    assert props["mode"]["default"] == "full"
    assert props["include_difficulty"]["type"] == "boolean"
    assert props["include_difficulty"]["default"] is True
    assert props["standalone"]["type"] == "boolean"
    assert props["standalone"]["default"] is False
    assert props["layout"]["enum"] == ["auto", "autocode", "standard_cn", "polygon", "hydro"]
    assert props["layout"]["default"] == "auto"
    assert _non_null(props["enable_mutation_testing"])["type"] == "boolean"
    assert props["strict"]["type"] == "boolean"
    assert props["strict"]["default"] is False
    # report_path is optional (absent from required, nullable string)
    assert "report_path" not in schema["required"]
    assert _non_null(props["report_path"])["type"] == "string"
    assert schema["required"] == ["problem_dir"]


def test_audit_and_verify_schema_parity_with_execute_and_skills():
    import inspect

    sig = inspect.signature(ProblemAuditTool.execute)
    exec_params = {
        name for name in sig.parameters if name != "self"
    }
    assert set(ProblemAuditInput.model_fields) == exec_params
    assert "strict" in ProblemAuditInput.model_fields

    # Every migrated Pydantic input model's fields must be accepted by its tool's execute()
    for tool_cls, model in _MIGRATED:
        t_sig = inspect.signature(tool_cls.execute)
        t_params = {name for name in t_sig.parameters if name != "self"}
        assert set(model.model_fields) <= t_params, f"{tool_cls.__name__} missing model fields from {model.__name__}"

    # ProblemVerifyTestsInput must accept every verify_types value referenced in skills/ & agents/
    repo_root = Path(__file__).resolve().parent.parent
    referenced_verify_types: set[str] = {"coverage"}
    for md_dir in ("skills", "agents"):
        for md_file in (repo_root / md_dir).rglob("*.md"):
            text = md_file.read_text(encoding="utf-8")
            for m in re.finditer(r"verify_types\s*=\s*\[([^\]]+)\]", text):
                for lit in re.findall(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']', m.group(1)):
                    referenced_verify_types.add(lit)
    parsed = ProblemVerifyTestsInput(problem_dir="/tmp/p", verify_types=sorted(referenced_verify_types))
    assert set(parsed.verify_types) == referenced_verify_types


def test_pack_polygon_schema_export_formats():
    schema = input_schema_from_model(ProblemPackPolygonInput)
    props = schema["properties"]
    assert "export_formats" in props
    ef = props["export_formats"]
    assert ef["type"] == "array"
    assert ef["items"]["enum"] == ["polygon", "hydro", "qoj"]
    assert ef["default"] == ["polygon"]


def test_file_read_schema_from_model():
    schema = input_schema_from_model(FileReadInput)
    assert {"path", "problem_dir"}.issubset(schema["properties"])
    assert schema["required"] == ["path"]


def test_file_save_schema_from_model():
    schema = input_schema_from_model(FileSaveInput)
    assert set(schema["properties"]) == {"path", "content", "problem_dir"}
    assert schema["required"] == ["path", "content"]


def test_validate_schema_nested_and_enum():
    schema = input_schema_from_model(ProblemValidateInput)
    props = schema["properties"]
    # validate_types: array of enum with default
    vt = props["validate_types"]
    assert vt["type"] == "array"
    assert vt["items"]["enum"] == ["statement_samples", "sample_files", "all"]
    assert vt["default"] == ["all"]
    # statement_samples: Optional array of inlined object
    ss = _non_null(props["statement_samples"])
    assert ss["type"] == "array"
    ss_item = ss["items"]
    assert ss_item["type"] == "object"
    assert set(ss_item["properties"]) == {"input", "expected_output"}
    assert ss_item["required"] == ["input"]
    # numeric defaults
    assert props["tolerance"]["type"] == "number"
    assert props["tolerance"]["default"] == 1e-9
    assert props["timeout"]["type"] == "integer"
    assert props["timeout"]["default"] == 30
    assert schema["required"] == ["problem_dir"]
    # nested models must be inlined, no leftover $ref / $defs
    assert "$defs" not in schema
    assert "$ref" not in json.dumps(schema)


def test_no_title_leaks():
    schema = input_schema_from_model(ProblemValidateInput)
    assert "title" not in schema
    for prop in schema["properties"].values():
        assert "title" not in prop
        resolved = _non_null(prop)
        if resolved.get("type") == "array":
            item = resolved["items"]
            if isinstance(item, dict) and item.get("type") == "object":
                for sub in item["properties"].values():
                    assert "title" not in sub


def test_model_defaults():
    a = ProblemAuditInput(problem_dir="x")
    assert a.mode == "full"
    assert a.include_difficulty is True
    assert a.report_path is None
    v = ProblemValidateInput(problem_dir="y")
    assert v.validate_types == ["all"]
    assert v.tolerance == 1e-9
    assert v.timeout == 30
    assert v.statement_samples is None


def test_statement_sample_model():
    s = StatementSample(input="1 2", expected_output="3")
    assert s.input == "1 2"
    assert s.expected_output == "3"


def test_migrated_tools_expose_derived_schema():
    # The tool's input_schema is the derived schema (single source of truth).
    assert ProblemAuditTool().input_schema == input_schema_from_model(ProblemAuditInput)
    assert FileReadTool().input_schema == input_schema_from_model(FileReadInput)
    assert FileSaveTool().input_schema == input_schema_from_model(FileSaveInput)
    assert ProblemValidateTool().input_schema == input_schema_from_model(ProblemValidateInput)


# Every migrated tool paired with the Pydantic model that is its single source of truth.
_MIGRATED = [
    (ProblemAuditTool, ProblemAuditInput),
    (FileReadTool, FileReadInput),
    (FileSaveTool, FileSaveInput),
    (ProblemValidateTool, ProblemValidateInput),
    (CheckerBuildTool, CheckerBuildInput),
    (ProblemBuildAllTool, ProblemBuildAllInput),
    (InteractorBuildTool, InteractorBuildInput),
    (GeneratorBuildTool, GeneratorBuildInput),
    (GeneratorRunTool, GeneratorRunInput),
    (ValidatorBuildTool, ValidatorBuildInput),
    (ValidatorSelectTool, ValidatorSelectInput),
    (SolutionAnalyzeTool, SolutionAnalyzeInput),
    (SolutionBuildTool, SolutionBuildInput),
    (SolutionRunTool, SolutionRunInput),
    (ProblemVerifyTestsTool, ProblemVerifyTestsInput),
    (StressTestRunTool, StressTestRunInput),
    (SolutionAuditStdTool, SolutionAuditStdInput),
    (SolutionAuditBruteTool, SolutionAuditBruteInput),
    (ProblemCreateTool, ProblemCreateInput),
    (ProblemGenerateTestsTool, ProblemGenerateTestsInput),
    (ProblemCleanupProcessesTool, ProblemCleanupProcessesInput),
    (ProblemPackPolygonTool, ProblemPackPolygonInput),
]


def test_all_migrated_tools_use_derived_schema():
    # Each migrated tool's input_schema MUST equal the model-derived schema:
    # the Pydantic model is the single source of truth, no hand-written dict.
    for tool_cls, model in _MIGRATED:
        derived = input_schema_from_model(model)
        assert tool_cls().input_schema == derived, f"{tool_cls.__name__} schema drifted"


def test_all_migrated_schemas_are_self_contained():
    # Derived schemas must be client-friendly: no $ref / $defs, and a top-level object.
    for _tool_cls, model in _MIGRATED:
        schema = input_schema_from_model(model)
        assert schema["type"] == "object"
        assert "$defs" not in schema
        assert "$ref" not in json.dumps(schema)


def test_stress_generator_args_defaults():
    # Verify nested StressGeneratorArgs inlines cleanly (used by StressTestRunInput).
    schema = input_schema_from_model(StressGeneratorArgs)
    assert schema["type"] == "object"
    assert schema["properties"]["type"]["default"] == "2"
    assert schema["properties"]["extra_args"]["default"] == []
    assert "$defs" not in schema
