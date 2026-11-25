"""Self-contained zero-CDN MCP Apps UI resources (``ui://autocode/*``)."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource
from pydantic import AnyUrl

MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"

_UI_APP_META: dict[str, Any] = {
    "ui": {
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
        },
        "prefersBorder": True,
    }
}


@dataclass(frozen=True)
class UiResourceSpec:
    """Descriptor for a self-contained MCP App HTML resource."""

    uri: str
    name: str
    title: str
    description: str
    filename: str
    mime_type: str = MCP_APP_MIME_TYPE


UI_RESOURCES: tuple[UiResourceSpec, ...] = (
    UiResourceSpec(
        uri="ui://autocode/complexity-plot",
        name="complexity-plot",
        title="AutoCode Complexity Plot",
        description=(
            "Interactive log-log empirical time complexity curve, OLS slope alpha, "
            "R^2 fit, and hardware cache-jump inspector."
        ),
        filename="complexity_plot.html",
    ),
    UiResourceSpec(
        uri="ui://autocode/audit-dashboard",
        name="audit-dashboard",
        title="AutoCode Audit Dashboard",
        description=(
            "Interactive wrong-solution kill ratio matrix, surviving mutants viewer, "
            "and problem quality/structural signals board."
        ),
        filename="audit_dashboard.html",
    ),
    UiResourceSpec(
        uri="ui://autocode/protocol-replay",
        name="protocol-replay",
        title="AutoCode Protocol Replay",
        description=(
            "Step-by-step interactive and communication judge <-> contestant "
            "transcript player with query/bit budget telemetry."
        ),
        filename="protocol_replay.html",
    ),
)

_UI_BY_URI: dict[str, UiResourceSpec] = {spec.uri: spec for spec in UI_RESOURCES}


def load_ui_html(filename: str) -> str:
    """Load an embedded MCP App HTML file via ``importlib.resources``."""
    return files("autocode_mcp.ui").joinpath(filename).read_text(encoding="utf-8")


def list_ui_resources() -> list[Resource]:
    """Return MCP ``Resource`` metadata descriptors for all ``ui://autocode/*`` apps."""
    resources: list[Resource] = []
    for spec in UI_RESOURCES:
        html = load_ui_html(spec.filename)
        resources.append(
            Resource(
                uri=AnyUrl(spec.uri),
                name=spec.name,
                title=spec.title,
                description=spec.description,
                mimeType=spec.mime_type,
                size=len(html.encode("utf-8")),
                _meta=_UI_APP_META,
            )
        )
    return resources


def read_ui_resource(uri: str | AnyUrl) -> list[ReadResourceContents]:
    """Return ``ReadResourceContents`` for a ``ui://autocode/*`` MCP App URI."""
    uri_str = str(uri)
    spec = _UI_BY_URI.get(uri_str)
    if spec is None:
        raise ValueError(f"Unknown UI resource URI: {uri_str}")
    html = load_ui_html(spec.filename)
    return [
        ReadResourceContents(
            content=html,
            mime_type=spec.mime_type,
            meta=_UI_APP_META,
        )
    ]
