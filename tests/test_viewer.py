"""Tests for agentcad.viewer."""

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pytest
import trimesh

from agentcad.viewer import parse_bed, parse_color_rule, start


def test_parse_color_rule() -> None:
    # Basic color
    pattern, color, opacity = parse_color_rule("shell=#c0392b")
    assert pattern == "shell"
    assert color == "#c0392b"
    assert opacity == 1.0

    # Color with opacity
    pattern, color, opacity = parse_color_rule("diffuser=#ffffff@0.35")
    assert pattern == "diffuser"
    assert color == "#ffffff"
    assert opacity == 0.35

    # Case insensitive pattern
    pattern, color, opacity = parse_color_rule("SHELL=#ff0000")
    assert pattern == "shell"

    # Bad inputs
    with pytest.raises(argparse.ArgumentTypeError):
        parse_color_rule("shell")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_color_rule("shell=red")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_color_rule("shell=#gg0000")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_color_rule("shell=#ff0000@1.5")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_color_rule("shell=#ff0000@-0.1")


def test_parse_bed() -> None:
    assert parse_bed("350x350") == [350.0, 350.0]
    assert parse_bed("220x250") == [220.0, 250.0]
    assert parse_bed("300.5x200.25") == [300.5, 200.25]
    assert parse_bed("300X200") == [300.0, 200.0]
    assert parse_bed(None) is None
    assert parse_bed("") is None

    with pytest.raises(argparse.ArgumentTypeError):
        parse_bed("350")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_bed("350x")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_bed("abc")


def create_test_stl(path: Path) -> trimesh.Trimesh:
    """Create a simple box STL for testing."""
    box = trimesh.creation.box(extents=[10, 20, 30])
    box.export(path)
    return box


def test_viewer_server(tmp_path: Path) -> None:
    # Create a test STL
    stl_path = tmp_path / "test_box.stl"
    create_test_stl(stl_path)

    # Start server on a free port
    server, url = start([str(stl_path)], open_browser=False)
    base_url = url.rstrip("/")

    try:
        # Wait a moment for server to be ready
        time.sleep(0.5)

        # Test root endpoint returns HTML
        with urllib.request.urlopen(f"{base_url}/") as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "three" in html.lower()
            assert "agentcad view" in html

        # Test API files endpoint
        with urllib.request.urlopen(f"{base_url}/api/files") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "files" in data
            assert len(data["files"]) == 1
            file_info = data["files"][0]
            assert file_info["name"] == "test_box.stl"
            assert file_info["size"] > 0
            assert file_info["id"] == 0

        # Test file serving
        with urllib.request.urlopen(f"{base_url}/files/0") as resp:
            assert resp.status == 200
            content = resp.read()
            assert content == stl_path.read_bytes()

        # Test 404 for non-existent file
        try:
            urllib.request.urlopen(f"{base_url}/files/999")
            assert False, "Expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404

        # Test directory traversal protection (path outside served set)
        try:
            urllib.request.urlopen(f"{base_url}/files/-1")
            assert False, "Expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_server_multiple_files(tmp_path: Path) -> None:
    # Create multiple test STLs
    stl1 = tmp_path / "box1.stl"
    stl2 = tmp_path / "box2.stl"
    create_test_stl(stl1)
    box2 = trimesh.creation.box(extents=[5, 5, 5])
    box2.export(stl2)

    server, url = start([str(stl1), str(stl2)], open_browser=False)
    base_url = url.rstrip("/")

    try:
        time.sleep(0.5)

        with urllib.request.urlopen(f"{base_url}/api/files") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert len(data["files"]) == 2
            names = {f["name"] for f in data["files"]}
            assert names == {"box1.stl", "box2.stl"}

        # Both files should be accessible
        for fid in (0, 1):
            with urllib.request.urlopen(f"{base_url}/files/{fid}") as resp:
                assert resp.status == 200

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_color_rules(tmp_path: Path) -> None:
    stl_path = tmp_path / "shell.stl"
    create_test_stl(stl_path)

    server, url = start([str(stl_path)], open_browser=False, colors=[("shell", "#ff0000", 0.5)])
    base_url = url.rstrip("/")

    try:
        time.sleep(0.5)

        with urllib.request.urlopen(f"{base_url}/api/files") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            file_info = data["files"][0]
            assert file_info["color"] == "#ff0000"
            assert file_info["opacity"] == 0.5

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_bed_outline(tmp_path: Path) -> None:
    stl_path = tmp_path / "test.stl"
    create_test_stl(stl_path)

    server, url = start([str(stl_path)], open_browser=False, bed=(300.0, 200.0))
    base_url = url.rstrip("/")

    try:
        time.sleep(0.5)

        with urllib.request.urlopen(f"{base_url}/api/files") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["bed"] == [300.0, 200.0]

    finally:
        server.shutdown()
        server.server_close()