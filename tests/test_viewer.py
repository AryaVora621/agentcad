"""Tests for agentcad.viewer."""

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
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


def _get_port_from_url(url: str) -> int:
    return int(url.rsplit(":", 1)[1].rstrip("/"))


def test_viewer_host_header_allowed_127(tmp_path: Path) -> None:
    stl_path = tmp_path / "test.stl"
    create_test_stl(stl_path)

    server, url = start([str(stl_path)], open_browser=False)
    base_url = url.rstrip("/")
    port = _get_port_from_url(url)

    try:
        time.sleep(0.5)

        req = urllib.request.Request(f"{base_url}/", headers={"Host": f"127.0.0.1:{port}"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_host_header_allowed_localhost(tmp_path: Path) -> None:
    stl_path = tmp_path / "test.stl"
    create_test_stl(stl_path)

    server, url = start([str(stl_path)], open_browser=False)
    base_url = url.rstrip("/")
    port = _get_port_from_url(url)

    try:
        time.sleep(0.5)

        req = urllib.request.Request(f"{base_url}/", headers={"Host": f"localhost:{port}"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_host_header_forbidden_evil(tmp_path: Path) -> None:
    stl_path = tmp_path / "test.stl"
    create_test_stl(stl_path)

    server, url = start([str(stl_path)], open_browser=False)
    base_url = url.rstrip("/")
    port = _get_port_from_url(url)

    try:
        time.sleep(0.5)

        # evil host with correct port
        req = urllib.request.Request(f"{base_url}/", headers={"Host": f"evil.example:{port}"})
        try:
            urllib.request.urlopen(req)
            assert False, "Expected 403"
        except urllib.error.HTTPError as e:
            assert e.code == 403
            body = e.read().decode("utf-8")
            assert body == "agentcad view: forbidden host"

        # evil host without port
        req = urllib.request.Request(f"{base_url}/", headers={"Host": "evil.example"})
        try:
            urllib.request.urlopen(req)
            assert False, "Expected 403"
        except urllib.error.HTTPError as e:
            assert e.code == 403
            body = e.read().decode("utf-8")
            assert body == "agentcad view: forbidden host"

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_api_files_forbidden_wrong_host(tmp_path: Path) -> None:
    stl_path = tmp_path / "test.stl"
    create_test_stl(stl_path)

    server, url = start([str(stl_path)], open_browser=False)
    base_url = url.rstrip("/")
    port = _get_port_from_url(url)

    try:
        time.sleep(0.5)

        req = urllib.request.Request(f"{base_url}/api/files", headers={"Host": f"evil.example:{port}"})
        try:
            urllib.request.urlopen(req)
            assert False, "Expected 403"
        except urllib.error.HTTPError as e:
            assert e.code == 403
            body = e.read().decode("utf-8")
            assert body == "agentcad view: forbidden host"

    finally:
        server.shutdown()
        server.server_close()


def test_viewer_files_endpoint_forbidden_wrong_host_no_stl_bytes(tmp_path: Path) -> None:
    stl_path = tmp_path / "test.stl"
    create_test_stl(stl_path)
    stl_bytes = stl_path.read_bytes()

    server, url = start([str(stl_path)], open_browser=False)
    base_url = url.rstrip("/")
    port = _get_port_from_url(url)

    try:
        time.sleep(0.5)

        req = urllib.request.Request(f"{base_url}/files/0", headers={"Host": f"evil.example:{port}"})
        try:
            urllib.request.urlopen(req)
            assert False, "Expected 403"
        except urllib.error.HTTPError as e:
            assert e.code == 403
            body = e.read().decode("utf-8")
            assert body == "agentcad view: forbidden host"
            assert stl_bytes not in body.encode("utf-8")

    finally:
        server.shutdown()
        server.server_close()


MSG = "already in use"
CLI = "import sys; from agentcad.cli import main; sys.exit(main(sys.argv[1:]))"


def _busy(tmp_path: Path):
    stl = tmp_path / "box.stl"
    create_test_stl(stl)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return stl, sock, sock.getsockname()[1]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, timeout=60, check=False)


def test_cli_view_busy_port_exits_2_without_traceback(tmp_path: Path) -> None:
    stl, sock, port = _busy(tmp_path)
    with sock:
        r = _run(["-c", CLI, "view", str(stl), "--no-open", "--port", str(port)])
    assert r.returncode == 2
    assert MSG in r.stderr and "Traceback" not in r.stderr


def test_cli_view_busy_port_json(tmp_path: Path) -> None:
    stl, sock, port = _busy(tmp_path)
    with sock:
        r = _run(["-c", CLI, "view", str(stl), "--no-open", "--port", str(port), "--json"])
    doc = json.loads(r.stdout)
    assert r.returncode == 2 and doc["schema"] == "agentcad.error/1" and doc["exit_code"] == 2
    assert MSG in doc["error"]


def test_standalone_server_busy_port_exits_2_without_traceback(tmp_path: Path) -> None:
    stl, sock, port = _busy(tmp_path)
    with sock:
        r = _run(["-m", "agentcad.viewer.server", str(stl), "--no-open", "--port", str(port)])
    assert r.returncode == 2
    assert MSG in r.stderr and "Traceback" not in r.stderr


def test_start_busy_port_raises_instead_of_exiting(tmp_path: Path) -> None:
    from agentcad.viewer.server import PortInUseError

    stl, sock, port = _busy(tmp_path)
    with sock, pytest.raises(PortInUseError, match=MSG):
        start([str(stl)], port=port, open_browser=False)


def _listener(family: int, host: str, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(1)
    return sock


def test_busy_ports_on_other_address_families_are_skipped(tmp_path: Path) -> None:
    from agentcad.viewer.server import PortInUseError, _port_in_use, find_free_port

    stl = tmp_path / "box.stl"
    create_test_stl(stl)
    # Find a base port where both p (IPv6 wildcard) and p+1 (IPv4 wildcard) can be held.
    for base in range(20000, 20200, 4):
        socks = []
        try:
            socks.append(_listener(socket.AF_INET6, "::", base))
            socks.append(_listener(socket.AF_INET, "0.0.0.0", base + 1))
            break
        except OSError:
            for s in socks:
                s.close()
    else:
        pytest.skip("no free port pair for IPv6 and IPv4 listeners")
    try:
        got = find_free_port(base)
        assert got not in (base, base + 1) and not _port_in_use(got)
        for busy in (base, base + 1):
            with pytest.raises(PortInUseError):
                start([str(stl)], port=busy, open_browser=False)
    finally:
        for s in socks:
            s.close()


def test_restart_on_same_explicit_port(tmp_path: Path) -> None:
    stl = tmp_path / "box.stl"
    create_test_stl(stl)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    for _ in range(2):
        httpd, url = start([str(stl)], port=port, open_browser=False)
        assert url == f"http://127.0.0.1:{port}/"
        httpd.shutdown()
        httpd.server_close()


def test_empty_dir_raises_and_cli_exits_2(tmp_path: Path) -> None:
    from agentcad.viewer.server import NoMeshFilesError

    with pytest.raises(NoMeshFilesError, match="no supported mesh files"):
        start([str(tmp_path)], open_browser=False)
    r = _run(["-c", CLI, "view", str(tmp_path), "--no-open"])
    assert r.returncode == 2
    assert "no supported mesh files" in r.stderr and "Traceback" not in r.stderr
