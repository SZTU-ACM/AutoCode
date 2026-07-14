"""Make the hook modules importable from tests without package wiring."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
# Allow tests to import the MCP-side package (e.g. autocode_mcp.workflow.manifest)
# so the hook gate truth can be compared against the single source of truth.
sys.path.insert(0, str(REPO_ROOT / "src"))
