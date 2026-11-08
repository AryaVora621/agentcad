"""MCP server: agentcad's build, check, measure, render and drawing commands as tools on stdio.

Every tool builds an argv and runs ``agentcad.cli.main`` in this process with stdout captured, so a tool result
and its errors are exactly what the command line gives, minus the ~2.5 s CadQuery import per call.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Annotated


def _as_list(value):
    return [value] if isinstance(value, str) else value


try:  # module level because MCPServer evaluates the tools' annotations in this namespace
    import anyio
    from mcp.types import CallToolResult, ImageContent, TextContent
    from pydantic import BeforeValidator

    # The schema stays a plain string array (cheap in context) while a bare string, which agents often send,
    # is still accepted.
    Paths = Annotated[list[str], BeforeValidator(_as_list)]
except ImportError:
    anyio = CallToolResult = ImageContent = TextContent = Paths = None

_MISSING_EXTRA = ("the MCP server needs the mcp extra: uv tool install 'agentcad[mcp]' "
                  "(or pip install 'agentcad[mcp]')")
INSTRUCTIONS = ("agentcad builds CAD parts from code and measures the built geometry. Call api() first for exact "
                "names and signatures, skill() for the workflow. Never report a dimension, clearance or PASS you "
                "did not get from check, measure or drawing.")

_LOCK = threading.Lock()  # OCCT is not thread safe; blocking work also stays on the main thread (see build_server).

_viewer: dict = {}  # {"httpd": ..., "url": ..., "key": ...} for the one live viewer of this process
_state: dict = {"root": None}


class ToolFailure(Exception):
    """A usage mistake in a tool call; becomes an agentcad.error/1 result with exit code 2."""


def _compact(doc) -> str:
    return json.dumps(doc, separators=(",", ":"))


def _error_doc(message: str, code: int = 2) -> dict:
    return {"schema": "agentcad.error/1", "error": message, "exit_code": code}


# -- paths --------------------------------------------------------------------------------------------------------

def _resolve(path: str) -> Path:
    """Absolute, symlink-free path; refused when it leaves --root."""
    resolved = Path(os.path.expanduser(str(path))).resolve()
    root: Path | None = _state["root"]
    if root is not None and resolved != root and not resolved.is_relative_to(root):
        raise ToolFailure(f"{path} is outside --root {root}; use a path inside it")
    return resolved


def _resolve_all(paths: list[str]) -> list[str]:
    items = list(paths)
    if not items:
        raise ToolFailure("give at least one path")
    return [str(_resolve(p)) for p in items]


# -- running the command line in this process -----------------------------------------------------------------------

def _run(argv: list[str]) -> tuple[int, dict]:
    """Run ``agentcad <argv>`` in-process and return (exit code, parsed stdout JSON)."""
    from .cli import main

    out, err = io.StringIO(), io.StringIO()
    with _LOCK, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit:  # argparse rejects bad values by exiting
            lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
            message = (lines[-1] if lines else "invalid arguments").removeprefix("agentcad: error: ")
            return 2, _error_doc(message)
        except Exception as exc:  # noqa: BLE001 - a bug or model failure must not kill the server
            return 3, _error_doc(f"{type(exc).__name__}: {exc}", 3)
    text = out.getvalue()
    # A model.py that print()s lands in the captured text before the command's own document, which starts at
    # column 0 with "{" (nested lines are indented).
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n" and text[i + 1:i + 2] == "{"]
    for start in starts:
        try:
            return code, json.loads(text[start:])
        except ValueError:
            continue
    detail = err.getvalue().strip().splitlines()
    return code or 3, _error_doc(detail[-1] if detail else "the command printed no JSON", code or 3)


def _result(code: int, doc: dict, extra=()):
    # Exit 1 is a normal answer (a gate failed); 2 and 3 mean the call itself failed.
    failed = code in (2, 3) or doc.get("schema") == "agentcad.error/1"
    content = [TextContent(type="text", text=_compact(doc)), *extra]
    return CallToolResult(content=content, is_error=failed)


def _fail(exc: ToolFailure | Exception, code: int = 2):
    return _result(code, _error_doc(str(exc), code))


def _command(argv: list[str]):
    code, doc = _run(argv)
    return _result(code, doc)


def _png(path: str, long_edge: int):
    """Image content for a PNG file, downscaled so tool results stay small."""
    import base64

    from PIL import Image

    with Image.open(path) as img:
        img.load()
        if max(img.size) > long_edge:
            img.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
    return ImageContent(type="image", data=base64.b64encode(buf.getvalue()).decode(), mime_type="image/png")


def _scratch(kind: str) -> str:
    """Output folder for loose files; keeps ./renders and ./drawings out of the user's working folder."""
    return tempfile.mkdtemp(prefix=f"agentcad-{kind}-")


def _is_project(path: str) -> bool:
    from .cli import _is_project as is_project

    return is_project(path)


# -- viewer -------------------------------------------------------------------------------------------------------

def _stop_viewer() -> None:
    httpd = _viewer.pop("httpd", None)
    _viewer.clear()
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()


def _start_viewer(inputs: list[str]) -> str:
    from .build import build
    from .project import Project
    from .viewer.server import NoMeshFilesError, PortInUseError, start

    key = tuple(inputs)
    if _viewer.get("key") == key:
        return _viewer["url"]
    _stop_viewer()  # the handler class holds one file manager, so a second server would hijack the first
    colors, bed, paths = [], None, inputs
    if len(inputs) == 1 and _is_project(inputs[0]):
        project = Project.load(inputs[0])
        result = build(project)
        paths = [str(result.path / "assembly" / f"{name}.stl") for name in project.parts]
        colors = [(f"{name}.stl", p.color, 1.0) for name, p in project.parts.items() if p.color]
        bed = tuple(project.printer.bed_xyz[:2])
    try:
        httpd, url = start(paths, open_browser=False, bed=bed, colors=colors)
    except (PortInUseError, NoMeshFilesError) as exc:
        raise ToolFailure(str(exc)) from exc
    _viewer.update(httpd=httpd, url=url, key=key)
    return url


def _without_titles(node, in_properties: bool = False):
    """Trim a tool schema: no auto-generated titles, no "or null" on optional arguments. It costs context every session."""
    if isinstance(node, dict):
        options = node.get("anyOf")
        if not in_properties and isinstance(options, list) and len(options) == 2 and {"type": "null"} in options:
            # Optional argument: it is simply left out when unused, so "or null" only repeats that.
            kept = next(o for o in options if o != {"type": "null"})
            return _without_titles({**{k: v for k, v in node.items() if k not in ("anyOf", "default")}, **kept})
        return {k: _without_titles(v, k == "properties") for k, v in node.items()
                if in_properties or k != "title"}
    if isinstance(node, list):
        return [_without_titles(v) for v in node]
    return node


def _slim_schemas(server) -> None:
    # Presentation only (argument validation uses the pydantic model), so a changed SDK internal is skipped.
    manager = getattr(server, "_tool_manager", None)
    for tool in manager.list_tools() if manager else ():
        tool.parameters = _without_titles(tool.parameters)


# -- the server ---------------------------------------------------------------------------------------------------

def build_server():
    """The MCPServer with every tool, prompt and resource registered (importable for tests)."""
    from mcp.server.mcpserver import MCPServer

    from . import __version__
    from .project import BuildError, ConfigError
    from .skill_install import skill_dir

    server = MCPServer("agentcad", instructions=INSTRUCTIONS, version=__version__)

    # Tools stay async on purpose: the SDK would run a sync tool on a worker thread, and VTK offscreen
    # rendering aborts the whole process off the main thread on macOS. Blocking inline on the main thread
    # serialises calls too.
    @server.tool()
    async def api() -> CallToolResult:
        """Call this first: every agentcad command, CadQuery ctx helper, config key, gate and frame in one JSON document."""
        from .api import describe

        return _result(0, describe())

    @server.tool()
    async def new_project(dir: str, template: str = "blank") -> CallToolResult:
        """Create a project folder (model.py plus agentcad.toml) from a template: blank, bracket or box-with-lid. Edit model.py next, then call check."""
        try:
            return _command(["new", str(_resolve(dir)), "--template", template, "--json"])
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def build(project: str) -> CallToolResult:
        """Build a project and write STEP, STL and a measured manifest to project/build. Returns the manifest; use check to judge it."""
        try:
            return _command(["build", str(_resolve(project)), "--json"])
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def check(project: str, only: str | None = None, brief: bool = True) -> CallToolResult:
        """Build the project, run every gate and its own checks. brief=true lists only checks needing attention, with fix hints. only: comma separated check ids. Read every FAIL before claiming a result."""
        try:
            argv = ["check", str(_resolve(project)), "--json"]
            if brief:
                argv.append("--brief")
            if only:
                argv += ["--only", only]
            return _command(argv)
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def render(inputs: Paths, views: str = "iso,front,right,top", frame: str = "use",
                     section: str | None = None) -> CallToolResult:
        """Render PNG views of a project folder or STEP/STL files and return the sheet as an image. frame: use or print. section: cut plane such as z=10. Renders catch gross errors, they never prove a dimension."""
        try:
            paths = _resolve_all(inputs)
            argv = ["render", *paths, "--views", views, "--frame", frame, "--json"]
            if section:
                argv += ["--section", section]
            if not (len(paths) == 1 and _is_project(paths[0])):
                argv += ["--out", _scratch("render")]
            code, doc = _run(argv)
            if code != 0 or doc.get("schema") == "agentcad.error/1":
                return _result(code, doc)
            files = doc.get("files", [])
            sheet = next((f for f in files if f.endswith("sheet.png")), files[0] if files else None)
            return _result(code, doc, [_png(sheet, 1024)] if sheet else ())
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def drawing(inputs: Paths, parts: str | None = None, projection: str = "third",
                      sheet: str = "auto") -> CallToolResult:
        """Dimensioned 2D drawing sheets of a project or STEP files: an image per sheet plus the measured dimensions as JSON. projection: third or first. sheet: auto, A4, A3, letter, tabloid."""
        try:
            paths = _resolve_all(inputs)
            argv = ["drawing", *paths, "--projection", projection, "--sheet", sheet, "--json"]
            if parts:
                argv += ["--parts", parts]
            if not (len(paths) == 1 and _is_project(paths[0])):
                argv += ["--out", _scratch("drawing")]
            code, doc = _run(argv)
            if code != 0 or doc.get("schema") == "agentcad.error/1":
                return _result(code, doc)
            images = [_png(s["files"]["png"], 1400) for s in doc.get("sheets", []) if "png" in s.get("files", {})]
            return _result(code, doc, images)
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def measure(files: Paths) -> CallToolResult:
        """Measure STEP or mesh files: bounding box, volume, area, centre of mass, validity."""
        try:
            return _command(["measure", *_resolve_all(files), "--json"])
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def interfere(files: Paths, tol: float = 0.01) -> CallToolResult:
        """Pairwise overlap volume between two or more files as placed (assembly check). tol: overlap allowed in mm3."""
        try:
            return _command(["interfere", *_resolve_all(files), "--tol", str(tol), "--json"])
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def overhang(files: Paths, max_deg: float = 45, allow: float = 25,
                       first_layer: float = 0.3) -> CallToolResult:
        """Unsupported downward-facing area in the print frame (bed at z=0). max_deg: tilt needing support; allow: mm2 tolerated; first_layer: mm ignored at the bed."""
        try:
            return _command(["overhang", *_resolve_all(files), "--max-deg", str(max_deg), "--allow", str(allow),
                             "--first-layer", str(first_layer), "--json"])
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def wall(files: Paths, thin: float = 1.9, bands: float = 0.0, up: bool = False,
                   zmin: float | None = None, zmax: float | None = None) -> CallToolResult:
        """Ray-cast wall thickness: minimum, percentiles and share of surface under thin (mm). bands: also tabulate per z band of this height; up: upward faces only; zmin/zmax: limit z."""
        try:
            argv = ["wall", *_resolve_all(files), "--thin", str(thin), "--bands", str(bands), "--json"]
            if up:
                argv.append("--up")
            for flag, value in (("--zmin", zmin), ("--zmax", zmax)):
                if value is not None:
                    argv += [flag, str(value)]
            return _command(argv)
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def fit(files: Paths, printer: str | None = None, bed: str | None = None) -> CallToolResult:
        """Does each file fit a printer bed? Give printer (a name from printers) or bed as XxYxZ in mm, for example 220x220x250."""
        try:
            if bool(printer) == bool(bed):
                raise ToolFailure("give exactly one of printer (see printers) or bed (for example 220x220x250)")
            argv = ["fit", *_resolve_all(files), "--json"]
            argv += ["--printer", printer] if printer else ["--bed", str(bed)]
            return _command(argv)
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def diff(old: str, new: str) -> CallToolResult:
        """Compare two builds (a manifest.json or a build folder each): per-part changes in size, volume and triangles."""
        try:
            return _command(["diff", str(_resolve(old)), str(_resolve(new)), "--json"])
        except ToolFailure as exc:
            return _fail(exc)

    @server.tool()
    async def printers() -> CallToolResult:
        """List the known printer profiles with bed size and nozzle."""
        return _command(["printers", "--json"])

    @server.tool()
    async def skill(reference: str | None = None) -> CallToolResult:
        """The agentcad workflow guide (SKILL.md), or one reference file by name: cadquery-patterns, design-for-printing, verification-playbook."""
        try:
            return _skill_result(reference)
        except (ConfigError, ToolFailure) as exc:
            return _fail(exc)

    @server.tool()
    async def view(inputs: Paths) -> CallToolResult:
        """Start the live 3D viewer on a project or mesh files and return its http://127.0.0.1:PORT/ URL for the human to open. A second call with different inputs restarts it."""
        try:
            with _LOCK:
                url = _start_viewer(_resolve_all(inputs))
            return _result(0, {"schema": "agentcad.view/1", "url": url})
        except ToolFailure as exc:
            return _fail(exc)
        except (ConfigError, BuildError) as exc:
            return _fail(exc, 3 if isinstance(exc, BuildError) else 2)

    def _skill_text(reference: str | None) -> str:
        root = skill_dir()
        if reference is None:
            return (root / "SKILL.md").read_text(encoding="utf-8")
        names = sorted(p.stem for p in (root / "references").glob("*.md"))
        name = reference.removesuffix(".md")
        if name not in names:
            raise ToolFailure(f"unknown reference {reference!r}; use one of: {', '.join(names)}")
        return (root / "references" / f"{name}.md").read_text(encoding="utf-8")

    def _skill_result(reference: str | None):
        return CallToolResult(content=[TextContent(type="text", text=_skill_text(reference))])

    # The skill is also a prompt and resources for hosts that have no skill folder.
    @server.prompt(name="agentcad-workflow", description="The agentcad design, check and verify workflow.")
    def workflow() -> str:
        return _skill_text(None)

    def _add_resource(uri: str, reference: str | None) -> None:
        @server.resource(uri, name=uri.rsplit("/", 1)[-1], mime_type="text/markdown")
        def read() -> str:
            return _skill_text(reference)

    _add_resource("agentcad://skill/SKILL.md", None)
    for ref in sorted(p.stem for p in (skill_dir() / "references").glob("*.md")):
        _add_resource(f"agentcad://skill/references/{ref}.md", ref)
    _slim_schemas(server)
    return server


# -- entry point --------------------------------------------------------------------------------------------------

def _claim_stdout():
    """Move the protocol to a private copy of fd 1 and aim fd 1 and sys.stdout at stderr.

    OCCT and VTK write to fd 1 from C and model.py files may print(); any such byte on the real stdout would
    corrupt the JSON-RPC stream.
    """
    sys.stdout.flush()
    wire = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return io.TextIOWrapper(os.fdopen(wire, "wb", closefd=False), encoding="utf-8", write_through=True)


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad mcp``."""
    from .cli import UsageError

    if anyio is None:
        raise UsageError(_MISSING_EXTRA)
    if args.root:
        root = Path(os.path.expanduser(args.root)).resolve()
        if not root.is_dir():
            raise UsageError(f"--root {args.root} is not a folder")
        _state["root"] = root
    server = build_server()
    wire = _claim_stdout()

    async def serve() -> None:
        from mcp.server.stdio import stdio_server

        async with stdio_server(stdout=anyio.wrap_file(wire)) as (read_stream, write_stream):
            lowlevel = server._lowlevel_server
            await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())

    try:
        anyio.run(serve)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_viewer()
    return 0
