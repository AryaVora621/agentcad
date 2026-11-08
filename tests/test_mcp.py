"""The MCP server, driven over real stdio with the SDK's own client (a subprocess per scenario)."""
from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="needs the mcp extra")

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

NOISY_MODEL = '''
import os
import cadquery as cq
from agentcad import Part

print("noise on stdout")
os.write(1, b"junk straight to fd 1\\n")


def parts():
    return [Part("cube", cq.Workplane("XY").box(20, 20, 20, centered=(True, True, False)), material="PLA")]
'''


@asynccontextmanager
async def server(*extra: str, cwd: Path | None = None):
    params = StdioServerParameters(command=sys.executable, args=["-m", "agentcad.cli", "mcp", *extra],
                                   cwd=str(cwd) if cwd else None)
    async with Client(params) as client:
        yield client


def drive(scenario, *extra: str, cwd: Path | None = None):
    """Run ``async def scenario(client)`` against a fresh server and return its result."""
    box = []

    async def main():
        async with server(*extra, cwd=cwd) as client:
            box.append(await scenario(client))

    anyio.run(main)
    return box[0]


def text_of(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


def doc_of(result) -> dict:
    return json.loads(text_of(result))


def images_of(result) -> list[Image.Image]:
    return [Image.open(io.BytesIO(base64.b64decode(c.data))) for c in result.content if c.type == "image"]


def copy_example(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(EXAMPLES / name, target, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return target


def test_missing_extra_exits_two():
    """Without the SDK the command must say how to install it instead of a traceback."""
    code = (
        "import sys; sys.modules['mcp'] = None\n"
        "from agentcad.cli import main\n"
        "raise SystemExit(main(['mcp']))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert "the MCP server needs the mcp extra: uv tool install 'agentcad[mcp]'" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_surface_tools_prompts_resources_and_small_reads():
    async def scenario(client):
        tools = await client.list_tools()
        prompts = await client.list_prompts()
        resources = await client.list_resources()
        api = await client.call_tool("api", {})
        printers = await client.call_tool("printers", {})
        skill = await client.call_tool("skill", {})
        ref = await client.call_tool("skill", {"reference": "design-for-printing"})
        bad_ref = await client.call_tool("skill", {"reference": "nope"})
        prompt = await client.get_prompt("agentcad-workflow")
        resource = await client.read_resource("agentcad://skill/references/verification-playbook.md")
        return tools, prompts, resources, api, printers, skill, ref, bad_ref, prompt, resource

    tools, prompts, resources, api, printers, skill, ref, bad_ref, prompt, resource = drive(scenario)
    names = {t.name for t in tools.tools}
    assert names == {"api", "new_project", "build", "check", "render", "drawing", "measure", "interfere", "overhang",
                     "wall", "fit", "diff", "printers", "skill", "view"}
    # Descriptions and schemas are the product's context cost on every session; keep them lean.
    size = len(json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools.tools], separators=(',', ':')))
    assert size < 5500, size
    assert [p.name for p in prompts.prompts] == ["agentcad-workflow"]
    uris = {str(r.uri) for r in resources.resources}
    assert "agentcad://skill/SKILL.md" in uris
    assert "agentcad://skill/references/design-for-printing.md" in uris
    assert doc_of(api)["schema"].startswith("agentcad.api")
    assert not api.is_error
    assert doc_of(printers)["schema"] == "agentcad.printers/1"
    assert "agentcad" in text_of(skill).lower()
    assert "overhang" in text_of(ref).lower()
    assert bad_ref.is_error and "cadquery-patterns" in text_of(bad_ref)
    assert "agentcad" in prompt.messages[0].content.text.lower()
    assert resource.contents[0].text


def test_new_project_then_check_and_a_failing_check(tmp_path):
    broken = copy_example(tmp_path, "broken")

    async def scenario(client):
        created = await client.call_tool("new_project", {"dir": str(tmp_path / "fresh")})
        ok = await client.call_tool("check", {"project": str(tmp_path / "fresh")})
        full = await client.call_tool("check", {"project": str(tmp_path / "fresh"), "brief": False})
        only = await client.call_tool("check", {"project": str(tmp_path / "fresh"), "only": "cube_height", "brief": False})
        nulls = await client.call_tool("check", {"project": str(tmp_path / "fresh"), "only": None})
        failing = await client.call_tool("check", {"project": str(broken)})
        again = await client.call_tool("new_project", {"dir": str(tmp_path / "fresh")})
        return created, ok, full, only, failing, again, nulls

    created, ok, full, only, failing, again, nulls = drive(scenario)
    assert not created.is_error and doc_of(created)["template"] == "blank"
    brief = doc_of(ok)
    assert not ok.is_error
    assert brief["schema"] == "agentcad.report/1" and brief["exit_code"] == 0 and brief["status"] == "PASS"
    assert len(text_of(full)) > len(text_of(ok))
    assert [c["id"] for c in doc_of(only)["checks"]] == ["cube_height"]
    # Exit 1 (a gate failed) is an answer, not a tool error; the report carries the status and the hints.
    report = doc_of(failing)
    assert not failing.is_error
    assert report["exit_code"] == 1 and report["status"] == "FAIL"
    assert any(c.get("hint") for c in report["checks"])
    assert not nulls.is_error  # an explicit null for an optional argument is accepted
    assert again.is_error and doc_of(again)["schema"] == "agentcad.error/1" and doc_of(again)["exit_code"] == 2


def test_missing_project_and_bad_arguments_are_error_results(tmp_path):
    async def scenario(client):
        missing = await client.call_tool("check", {"project": str(tmp_path / "nope")})
        template = await client.call_tool("new_project", {"dir": str(tmp_path / "x"), "template": "zzz"})
        fit = await client.call_tool("fit", {"files": [str(tmp_path / "a.step")]})
        return missing, template, fit

    missing, template, fit = drive(scenario)
    for result, code in ((missing, 2), (template, 2), (fit, 2)):
        assert result.is_error
        doc = doc_of(result)
        assert doc["schema"] == "agentcad.error/1" and doc["exit_code"] == code and doc["error"]
        assert "Traceback" not in doc["error"]


def test_model_that_raises_is_an_error_result_and_server_survives(make_project):
    folder = make_project("def parts():\n    raise RuntimeError('boom')\n")

    async def scenario(client):
        broken = await client.call_tool("check", {"project": str(folder)})
        after = await client.call_tool("printers", {})
        return broken, after

    broken, after = drive(scenario)
    assert broken.is_error and doc_of(broken)["exit_code"] == 3
    assert "boom" in text_of(broken)
    assert not after.is_error


def test_root_refuses_paths_outside(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (inside / "link").symlink_to(outside)

    async def scenario(client):
        ok = await client.call_tool("new_project", {"dir": str(inside / "p")})
        refused = await client.call_tool("check", {"project": str(outside)})
        escaped = await client.call_tool("check", {"project": str(inside / ".." / "outside")})
        via_link = await client.call_tool("build", {"project": str(inside / "link")})
        listed = await client.call_tool("measure", {"files": [str(outside / "x.step")]})
        return ok, refused, escaped, via_link, listed

    ok, refused, escaped, via_link, listed = drive(scenario, "--root", str(inside))
    assert not ok.is_error
    for result in (refused, escaped, via_link, listed):
        assert result.is_error
        assert "is outside --root" in doc_of(result)["error"]
        assert str(inside.resolve()) in doc_of(result)["error"]


def test_stdout_noise_from_model_code_does_not_break_the_protocol(make_project):
    folder = make_project(NOISY_MODEL)

    async def scenario(client):
        first = await client.call_tool("check", {"project": str(folder)})
        second = await client.call_tool("build", {"project": str(folder)})
        return first, second

    first, second = drive(scenario)
    assert not first.is_error and doc_of(first)["status"] == "PASS"
    assert not second.is_error and doc_of(second)["parts"]


def test_editing_model_between_checks_changes_the_second_result(make_project):
    source = '''
import cadquery as cq
from agentcad import Part

HEIGHT = {h}


def parts():
    return [Part("cube", cq.Workplane("XY").box(20, 20, HEIGHT, centered=(True, True, False)), material="PLA")]


def checks(ctx):
    ctx.expect("height", ctx.solid("cube", "print").BoundingBox().zlen, min=19.9, max=20.1, part="cube")
'''
    folder = make_project(source.format(h=20))

    async def scenario(client):
        first = await client.call_tool("check", {"project": str(folder)})
        (folder / "model.py").write_text(source.format(h=30))
        second = await client.call_tool("check", {"project": str(folder), "only": "height"})
        return first, second

    first, second = drive(scenario)
    assert doc_of(first)["status"] == "PASS"
    changed = doc_of(second)
    assert changed["status"] == "FAIL" and changed["checks"][0]["measured"]["value_mm"] == pytest.approx(30, abs=0.01)


def test_file_tools_measure_and_fit(tmp_path):
    bracket = copy_example(tmp_path, "bracket")

    async def scenario(client):
        built = await client.call_tool("build", {"project": str(bracket)})
        step = next((bracket / "build").rglob("*.step"))
        measured = await client.call_tool("measure", {"files": str(step)})
        fits = await client.call_tool("fit", {"files": [str(step)], "bed": "220x220x250"})
        return built, measured, fits

    built, measured, fits = drive(scenario)
    assert not built.is_error
    assert doc_of(measured)["checks"][0]["measured"]["volume_mm3"] > 0
    assert doc_of(fits)["status"] == "PASS"


@pytest.mark.render
@pytest.mark.slow
def test_render_returns_a_downscaled_png(tmp_path):
    bracket = copy_example(tmp_path, "bracket")

    async def scenario(client):
        return await client.call_tool("render", {"inputs": str(bracket)})

    result = drive(scenario)
    assert not result.is_error
    images = images_of(result)
    assert len(images) == 1 and images[0].format == "PNG"
    assert max(images[0].size) <= 1024
    assert any(f.endswith("sheet.png") for f in doc_of(result)["files"])


@pytest.mark.slow
def test_drawing_returns_images_and_dimensions(tmp_path):
    bracket = copy_example(tmp_path, "bracket")

    async def scenario(client):
        return await client.call_tool("drawing", {"inputs": str(bracket)})

    result = drive(scenario)
    assert not result.is_error
    images = images_of(result)
    doc = doc_of(result)
    assert images and all(max(i.size) <= 1400 for i in images)
    assert len(images) == len(doc["sheets"])
    assert doc["sheets"][0]["dimensions"]


@pytest.mark.slow
def test_view_starts_and_restarts_the_viewer(tmp_path):
    bracket = copy_example(tmp_path, "bracket")

    async def scenario(client):
        first = await client.call_tool("view", {"inputs": str(bracket)})
        same = await client.call_tool("view", {"inputs": str(bracket)})
        none = await client.call_tool("view", {"inputs": str(tmp_path / "empty")})
        return first, same, none

    first, same, none = drive(scenario)
    url = doc_of(first)["url"]
    assert url.startswith("http://127.0.0.1:") and doc_of(same)["url"] == url
    assert none.is_error


def test_pinned_sdk_still_has_the_internals_the_server_uses():
    # mcp_server.py feeds its own streams through _lowlevel_server and trims schemas through _tool_manager; the
    # extra is capped below mcp 2.4 because a release may rename them, and _slim_schemas would skip silently.
    from agentcad.mcp_server import build_server

    server = build_server()
    assert hasattr(server._lowlevel_server, "run") and hasattr(server._lowlevel_server, "create_initialization_options")
    tools = server._tool_manager.list_tools()
    assert len(tools) == 15
    assert all("title" not in json.dumps(t.parameters) for t in tools)  # the schema trim actually ran
