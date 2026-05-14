from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, Resource, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from .engine import (
    CompositionSpec,
    EffectSpec,
    effect_catalog,
    process_audio_file,
    render_composition,
)
from .projects import (
    CreateProjectRequest,
    ProjectClipSpec,
    ProjectStore,
    ProjectTrackSpec,
    RenderProjectRequest,
    RenderRegionRequest,
    UpdateProjectRequest,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT / "public"
OUTPUT_DIR = ROOT / "outputs"
PROJECT_DIR = ROOT / "projects"
WIDGET_URI = "ui://widget/pedalboard-studio-v1.html"
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"


class ProcessFileRequest(BaseModel):
    input_path: str = Field(description="Path to a local audio file reachable by the MCP server.")
    effects: list[EffectSpec] = Field(description="Pedalboard effect chain to apply.")
    output_name: str | None = Field(default=None, description="Optional output WAV name.")


class TrackMutationRequest(BaseModel):
    project_id: str
    track: ProjectTrackSpec


class ClipMutationRequest(BaseModel):
    project_id: str
    clip: ProjectClipSpec


class PedalboardMCP(FastMCP):
    def __init__(self) -> None:
        public_base_url = os.getenv("PUBLIC_BASE_URL", "")
        allowed_hosts = [
            "127.0.0.1",
            "127.0.0.1:8000",
            "127.0.0.1:8017",
            "localhost",
            "localhost:8000",
            "localhost:8017",
        ]
        if public_base_url.startswith(("http://", "https://")):
            allowed_hosts.append(public_base_url.split("://", 1)[1].rstrip("/"))

        super().__init__(
            "Robotic Pedalboard",
            instructions=(
                "Use this app from the normal ChatGPT conversation to compose, render, and "
                "process audio with Spotify Pedalboard. Return audio links directly in chat. "
                "Only open the optional studio widget when the user explicitly asks for a UI."
            ),
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8000")),
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
        )
        self.tool_meta: dict[str, dict[str, Any]] = {}
        self.tool_output_schemas: dict[str, dict[str, Any]] = {}
        self.resource_meta: dict[str, dict[str, Any]] = {}

    async def list_tools(self) -> list[Tool]:
        tools = self._tool_manager.list_tools()
        return [
            Tool(
                name=info.name,
                title=info.title,
                description=info.description,
                inputSchema=info.parameters,
                outputSchema=self.tool_output_schemas.get(info.name) or info.output_schema,
                annotations=info.annotations,
                icons=info.icons,
                _meta=self.tool_meta.get(info.name),
            )
            for info in tools
        ]

    async def list_resources(self) -> list[Resource]:
        resources = self._resource_manager.list_resources()
        return [
            Resource(
                uri=resource.uri,
                name=resource.name or "",
                title=resource.title,
                description=resource.description,
                mimeType=resource.mime_type,
                icons=resource.icons,
                _meta=self.resource_meta.get(str(resource.uri)),
            )
            for resource in resources
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        result = await self._tool_manager.call_tool(name, arguments, context=self.get_context())
        if isinstance(result, CallToolResult):
            return result
        return CallToolResult(content=[TextContent(type="text", text=str(result))])


mcp = PedalboardMCP()
project_store = ProjectStore(PROJECT_DIR)


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", f"http://localhost:{mcp.settings.port}")


def _tool_result(message: str, structured: dict[str, Any], meta: dict[str, Any] | None = None) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=structured,
        _meta=meta or {},
    )


@mcp.resource(
    WIDGET_URI,
    name="pedalboard-studio",
    title="Pedalboard Studio",
    description="Interactive ChatGPT studio for composing and processing audio with Pedalboard.",
    mime_type=RESOURCE_MIME_TYPE,
)
def pedalboard_widget() -> str:
    html = (PUBLIC_DIR / "pedalboard-studio-v1.html").read_text(encoding="utf-8")
    return html.replace("__EFFECT_CATALOG__", json.dumps(effect_catalog()))


mcp.resource_meta[WIDGET_URI] = {
    "ui": {
        "prefersBorder": True,
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
        },
    },
    "openai/widgetDescription": (
        "A compact music studio showing Pedalboard effects, render settings, and WAV outputs."
    ),
}


@mcp.tool(
    name="inspect_pedalboard_effects",
    title="Inspect Pedalboard Effects",
    description=(
        "Use this when you need the available Pedalboard effects, default parameters, and safe "
        "parameter ranges before designing an effect chain."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def inspect_pedalboard_effects() -> CallToolResult:
    effects = effect_catalog()
    return _tool_result(
        f"Pedalboard has {len(effects)} built-in effects ready for agentic rendering.",
        {"effects": effects, "effect_count": len(effects)},
    )


@mcp.tool(
    name="open_pedalboard_studio",
    title="Open Pedalboard Studio",
    description=(
        "Use this only when the user explicitly asks for the optional interactive widget; "
        "normal music generation should use render_pedalboard_composition directly."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def open_pedalboard_studio() -> CallToolResult:
    examples = [
        "dusty tape loop with a wide chorus and short room",
        "bright robotic arpeggio with delay and limiter",
        "slow industrial bass sketch with distortion and reverb",
    ]
    return _tool_result(
        "Opening the Pedalboard studio.",
        {
            "app": "Robotic Pedalboard",
            "effects": effect_catalog(),
            "examples": examples,
            "lastRender": None,
        },
        {"examples": examples},
    )


mcp.tool_meta["open_pedalboard_studio"] = {
    "ui": {"resourceUri": WIDGET_URI, "visibility": ["model", "app"]},
    "openai/outputTemplate": WIDGET_URI,
    "openai/toolInvocation/invoking": "Opening Pedalboard Studio...",
    "openai/toolInvocation/invoked": "Pedalboard Studio is ready.",
}


@mcp.tool(
    name="render_pedalboard_composition",
    title="Render Pedalboard Composition",
    description=(
        "Use this when the user wants ChatGPT to compose or render a new WAV using synthesized "
        "tracks and a Pedalboard effect chain."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def render_pedalboard_composition(
    composition: Annotated[CompositionSpec, Field(description="Composition, tracks, and effects to render.")]
) -> CallToolResult:
    result = render_composition(composition, OUTPUT_DIR, _public_base_url())
    return _tool_result(
        (
            f"Rendered {result['title']} as {result['file']['file_name']}. "
            f"Audio: {result['file']['download_url']}"
        ),
        {"render": result, "lastRender": result},
        {"local_path": result["local_path"]},
    )


mcp.tool_meta["render_pedalboard_composition"] = {
    "openai/toolInvocation/invoking": "Rendering audio through Pedalboard...",
    "openai/toolInvocation/invoked": "Audio render complete.",
}
mcp.tool_output_schemas["render_pedalboard_composition"] = {
    "type": "object",
    "properties": {"render": {"type": "object"}, "lastRender": {"type": "object"}},
    "required": ["render", "lastRender"],
}


@mcp.tool(
    name="create_project",
    title="Create Project",
    description=(
        "Use this when the user wants persistent music state that can be edited and rendered "
        "across multiple ChatGPT turns."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def create_project(request: CreateProjectRequest) -> CallToolResult:
    project = project_store.create(request)
    return _tool_result(
        f"Created project {project.project_id}: {project.title}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="get_project",
    title="Get Project",
    description="Use this when you need the current persistent project state before editing or rendering.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def get_project(project_id: str) -> CallToolResult:
    project = project_store.get(project_id)
    return _tool_result(
        f"Loaded project {project.project_id}: {project.title}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="update_project",
    title="Update Project",
    description=(
        "Use this when changing persistent project metadata, sections, tempo, key, duration, "
        "or master effects without replacing tracks and clips."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def update_project(request: UpdateProjectRequest) -> CallToolResult:
    project = project_store.update(request)
    return _tool_result(
        f"Updated project {project.project_id}: {project.title}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="add_project_track",
    title="Add Project Track",
    description="Use this when adding a durable track with a stable track_id to a project.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def add_project_track(request: TrackMutationRequest) -> CallToolResult:
    project = project_store.add_track(request.project_id, request.track)
    return _tool_result(
        f"Added track {request.track.track_id} to project {project.project_id}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="update_project_track",
    title="Update Project Track",
    description="Use this when changing a durable track's name, waveform, gain, pan, mute, or stored effects.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def update_project_track(request: TrackMutationRequest) -> CallToolResult:
    project = project_store.update_track(request.project_id, request.track)
    return _tool_result(
        f"Updated track {request.track.track_id} in project {project.project_id}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="add_project_clip",
    title="Add Project Clip",
    description="Use this when adding a durable clip with notes to a project track.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def add_project_clip(request: ClipMutationRequest) -> CallToolResult:
    project = project_store.add_clip(request.project_id, request.clip)
    return _tool_result(
        f"Added clip {request.clip.clip_id} to project {project.project_id}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="update_project_clip",
    title="Update Project Clip",
    description="Use this when editing a durable clip's bars, length, or note array.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def update_project_clip(request: ClipMutationRequest) -> CallToolResult:
    project = project_store.update_clip(request.project_id, request.clip)
    return _tool_result(
        f"Updated clip {request.clip.clip_id} in project {project.project_id}.",
        {"project": project.model_dump(mode="json")},
        {"project_path": str(PROJECT_DIR / f"{project.project_id}.json")},
    )


@mcp.tool(
    name="render_project",
    title="Render Project",
    description="Use this when rendering the current persistent project state to a full mix WAV.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def render_project(request: RenderProjectRequest) -> CallToolResult:
    project = project_store.get(request.project_id)
    composition = project_store.to_composition(project)
    result = render_composition(composition, OUTPUT_DIR, _public_base_url())
    project = project_store.attach_render(project.project_id, result["file"]["download_url"], "render_project")
    return _tool_result(
        (
            f"Rendered project {project.project_id} as {result['file']['file_name']}. "
            f"Audio: {result['file']['download_url']}"
        ),
        {"project": project.model_dump(mode="json"), "render": result, "lastRender": result},
        {"local_path": result["local_path"]},
    )


@mcp.tool(
    name="render_project_region",
    title="Render Project Region",
    description="Use this when rendering only a bar range from a persistent project for faster iteration.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def render_project_region(request: RenderRegionRequest) -> CallToolResult:
    if request.end_bar < request.start_bar:
        raise ValueError("end_bar must be greater than or equal to start_bar.")
    project = project_store.get(request.project_id)
    composition = project_store.to_composition(project, request.start_bar, request.end_bar)
    result = render_composition(composition, OUTPUT_DIR, _public_base_url())
    project = project_store.attach_render(project.project_id, result["file"]["download_url"], "render_project_region")
    return _tool_result(
        (
            f"Rendered bars {request.start_bar}-{request.end_bar} of {project.project_id} "
            f"as {result['file']['file_name']}. Audio: {result['file']['download_url']}"
        ),
        {"project": project.model_dump(mode="json"), "render": result, "lastRender": result},
        {"local_path": result["local_path"]},
    )


@mcp.tool(
    name="render_project_stems",
    title="Render Project Stems",
    description="Use this when exporting one WAV per unmuted project track plus the full mix.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def render_project_stems(request: RenderProjectRequest) -> CallToolResult:
    project = project_store.get(request.project_id)
    mix = render_composition(project_store.to_composition(project), OUTPUT_DIR, _public_base_url())
    stems = []
    for track_id, composition in project_store.stem_compositions(project):
        result = render_composition(composition, OUTPUT_DIR, _public_base_url())
        stems.append({"track_id": track_id, "render": result})
    project = project_store.attach_render(project.project_id, mix["file"]["download_url"], "render_project_stems")
    return _tool_result(
        f"Rendered {len(stems)} stems and a mix for project {project.project_id}.",
        {"project": project.model_dump(mode="json"), "mix": mix, "stems": stems},
        {"mix_local_path": mix["local_path"]},
    )


@mcp.tool(
    name="process_audio_with_pedalboard",
    title="Process Audio With Pedalboard",
    description=(
        "Use this when the user has a local audio file path and wants a Pedalboard chain applied "
        "to produce a new downloadable WAV."
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def process_audio_with_pedalboard(request: ProcessFileRequest) -> CallToolResult:
    result = process_audio_file(
        Path(request.input_path).expanduser(),
        request.effects,
        OUTPUT_DIR,
        _public_base_url(),
        request.output_name,
    )
    return _tool_result(
        (
            f"Processed {Path(request.input_path).name} as {result['file']['file_name']}. "
            f"Audio: {result['file']['download_url']}"
        ),
        {"render": result, "lastRender": result},
        {"local_path": result["local_path"]},
    )


mcp.tool_meta["process_audio_with_pedalboard"] = {
    "openai/toolInvocation/invoking": "Processing audio through Pedalboard...",
    "openai/toolInvocation/invoked": "Audio processing complete.",
}


@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def root(_: Request) -> Response:
    return JSONResponse(
        {
            "name": "Robotic Pedalboard",
            "mcp": "/mcp",
            "health": "/health",
        }
    )


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> Response:
    return JSONResponse({"ok": True, "mcp": "/mcp", "outputs": "/outputs/{file_name}"})


@mcp.custom_route("/widget", methods=["GET"], include_in_schema=False)
async def widget_preview(_: Request) -> Response:
    return Response(pedalboard_widget(), media_type="text/html")


@mcp.custom_route("/api/render", methods=["POST"], include_in_schema=False)
async def render_preview(request: Request) -> Response:
    try:
        payload = await request.json()
        composition = CompositionSpec.model_validate(payload.get("composition", payload))
        result = render_composition(composition, OUTPUT_DIR, _public_base_url())
        return JSONResponse({"structuredContent": {"render": result, "lastRender": result}})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@mcp.custom_route("/favicon.ico", methods=["GET"], include_in_schema=False)
async def favicon(_: Request) -> Response:
    return Response(status_code=204)


@mcp.custom_route("/outputs/{file_name:path}", methods=["GET"], include_in_schema=False)
async def output_file(request: Request) -> Response:
    requested = (OUTPUT_DIR / request.path_params["file_name"]).resolve()
    if OUTPUT_DIR.resolve() not in requested.parents and requested != OUTPUT_DIR.resolve():
        return JSONResponse({"error": "Invalid output path."}, status_code=400)
    if not requested.exists():
        return JSONResponse({"error": "Output file not found."}, status_code=404)
    return FileResponse(str(requested), media_type="audio/wav", filename=requested.name)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
