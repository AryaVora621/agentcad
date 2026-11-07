"""agentcad viewer server - local three.js mesh viewer with live reload."""

import argparse
import http.server
import json
import os
import re
import socket
import sys
import threading
import urllib.parse
import webbrowser
from importlib import resources
from typing import Any

SUPPORTED_EXTS = {".stl", ".obj", ".3mf", ".glb", ".gltf", ".ply"}
STEP_EXTS = {".step", ".stp"}

MIME_TYPES = {
    ".stl": "model/stl",
    ".obj": "text/plain",
    ".3mf": "model/3mf",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".ply": "model/ply",
}


def parse_color_rule(text: str) -> tuple[str, str, float]:
    """Parse PATTERN=COLOR[@OPACITY] into (pattern_lower, '#rrggbb', opacity)."""
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"--color expects PATTERN=#RRGGBB[@OPACITY], got {text!r}")
    pattern, spec = text.rsplit("=", 1)
    opacity = 1.0
    if "@" in spec:
        spec, op = spec.split("@", 1)
        try:
            opacity = float(op)
        except ValueError:
            raise argparse.ArgumentTypeError(f"bad opacity in {text!r}")
        if not 0.0 <= opacity <= 1.0:
            raise argparse.ArgumentTypeError(f"opacity must be 0..1 in {text!r}")
    if not pattern or not re.fullmatch(r"#[0-9a-fA-F]{6}", spec):
        raise argparse.ArgumentTypeError(f"--color expects PATTERN=#RRGGBB[@OPACITY], got {text!r}")
    return (pattern.lower(), spec.lower(), opacity)


def parse_bed(bed_str: str | None) -> list[float] | None:
    """Parse WxD bed dimension string into [width, depth]."""
    if not bed_str:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)$", bed_str.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid bed dimension '{bed_str}'. Expected format WxD (e.g. 350x350)")
    return [float(m.group(1)), float(m.group(2))]


def find_free_port(start_port: int = 8765) -> int:
    """Find a free port on 127.0.0.1 starting from start_port."""
    for port in range(start_port, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No available port found on 127.0.0.1")


class FileManager:
    """Manages the set of mesh files being served."""

    def __init__(
        self,
        raw_paths: list[str],
        *,
        recursive: bool = False,
        bed: list[float] | None = None,
        color_rules: list[tuple[str, str, float]] | None = None,
    ):
        self.raw_paths = raw_paths
        self.color_rules = color_rules or []
        self.recursive = recursive
        self.bed = bed
        self.lock = threading.Lock()
        self.path_to_id: dict[str, int] = {}
        self.id_to_path: dict[int, str] = {}
        self.next_id = 0
        self.step_hint_printed = False

        step_seen, initial_files = self._scan_paths()
        if step_seen and not self.step_hint_printed:
            sys.stderr.write("STEP not supported; export STL/GLB from your CAD tool\n")
            self.step_hint_printed = True

        if not initial_files:
            sys.stderr.write(f"Error: No supported mesh files found in: {', '.join(self.raw_paths)}\n")
            sys.exit(2)

        # Sort files by name initially so ID 0 corresponds to first file by name
        initial_files.sort(key=lambda p: (os.path.basename(p).lower(), p.lower()))
        for p in initial_files:
            self._assign_id(p)

    def _assign_id(self, p: str) -> int:
        if p not in self.path_to_id:
            fid = self.next_id
            self.next_id += 1
            self.path_to_id[p] = fid
            self.id_to_path[fid] = p
            return fid
        return self.path_to_id[p]

    def _scan_paths(self) -> tuple[bool, list[str]]:
        step_seen = False
        found: set[str] = set()
        for raw in self.raw_paths:
            if os.path.isfile(raw):
                ext = os.path.splitext(raw)[1].lower()
                if ext in STEP_EXTS:
                    step_seen = True
                elif ext in SUPPORTED_EXTS:
                    found.add(os.path.abspath(raw))
            elif os.path.isdir(raw):
                if self.recursive:
                    for root, _dirs, files in os.walk(raw):
                        for f in files:
                            ext = os.path.splitext(f)[1].lower()
                            if ext in STEP_EXTS:
                                step_seen = True
                            elif ext in SUPPORTED_EXTS:
                                found.add(os.path.abspath(os.path.join(root, f)))
                else:
                    try:
                        for entry in os.scandir(raw):
                            if entry.is_file(follow_symlinks=True):
                                ext = os.path.splitext(entry.name)[1].lower()
                                if ext in STEP_EXTS:
                                    step_seen = True
                                elif ext in SUPPORTED_EXTS:
                                    found.add(os.path.abspath(entry.path))
                    except OSError:
                        pass
        return step_seen, list(found)

    def _compute_rel(self, p: str) -> str:
        abs_p = os.path.abspath(p)
        try:
            rel = os.path.relpath(abs_p, os.getcwd())
            if not rel.startswith(".." + os.sep) and rel != "..":
                return rel
        except ValueError:
            pass
        for raw in self.raw_paths:
            abs_raw = os.path.abspath(raw)
            if os.path.isdir(abs_raw):
                base_dir = os.path.dirname(abs_raw.rstrip(os.sep))
                try:
                    rel = os.path.relpath(abs_p, base_dir)
                    if not rel.startswith(".." + os.sep) and rel != "..":
                        return rel
                except ValueError:
                    pass
            else:
                try:
                    rel = os.path.relpath(abs_p, os.path.dirname(abs_raw))
                    if not rel.startswith(".." + os.sep) and rel != "..":
                        return rel
                except ValueError:
                    pass
        return os.path.basename(p)

    def get_api_data(self) -> dict[str, Any]:
        """Return JSON-serializable file list and bed info."""
        with self.lock:
            step_seen, current_files = self._scan_paths()
            if step_seen and not self.step_hint_printed:
                sys.stderr.write("STEP not supported; export STL/GLB from your CAD tool\n")
                self.step_hint_printed = True

            for p in current_files:
                if p not in self.path_to_id:
                    self._assign_id(p)

            current_set = set(current_files)
            self.id_to_path = {fid: p for p, fid in self.path_to_id.items() if p in current_set}

            file_items = []
            for p in current_files:
                try:
                    st = os.stat(p)
                    fid = self.path_to_id[p]
                    lname = os.path.basename(p).lower()
                    rule = next((r for r in self.color_rules if r[0] in lname), None)
                    file_items.append({
                        "color": rule[1] if rule else None,
                        "opacity": rule[2] if rule else None,
                        "id": fid,
                        "name": os.path.basename(p),
                        "rel": self._compute_rel(p),
                        "size": st.st_size,
                        "mtime": round(st.st_mtime, 4),
                    })
                except OSError:
                    pass

            file_items.sort(key=lambda x: (x["name"].lower(), x["rel"].lower()))
            return {
                "files": file_items,
                "bed": self.bed,
            }

    def get_file_path(self, fid: int) -> str | None:
        """Return absolute path for a file ID, or None if not found."""
        with self.lock:
            p = self.id_to_path.get(fid)
            if p and os.path.isfile(p):
                return p
            return None


class ViewerHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for the viewer."""

    manager: FileManager | None = None

    def log_message(self, format: str, *args: Any) -> None:
        # Quiet logging: suppress per-request logs
        pass

    def send_not_found(self) -> None:
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b"404 Not Found")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            html_path = resources.files("agentcad.viewer") / "static" / "index.html"
            body = html_path.read_text(encoding="utf-8").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/files":
            if self.manager is None:
                self.send_not_found()
                return
            data = self.manager.get_api_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        m = re.fullmatch(r"/files/(\d+)", path)
        if m:
            if self.manager is None:
                self.send_not_found()
                return
            fid = int(m.group(1))
            file_path = self.manager.get_file_path(fid)
            if not file_path:
                self.send_not_found()
                return
            try:
                st = os.stat(file_path)
                ext = os.path.splitext(file_path)[1].lower()
                content_type = MIME_TYPES.get(ext, "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(st.st_size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(file_path, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return
            except OSError:
                self.send_not_found()
                return

        self.send_not_found()


def start(
    paths: list[str],
    *,
    port: int | None = None,
    open_browser: bool = True,
    bed: tuple[float, float] | None = None,
    colors: list[tuple[str, str, float]] = (),
    recursive: bool = False,
) -> tuple[http.server.ThreadingHTTPServer, str]:
    """Start the viewer server non-blocking. Returns (server, url)."""
    bed_list = list(bed) if bed else None
    manager = FileManager(paths, recursive=recursive, bed=bed_list, color_rules=list(colors))

    if port is None:
        port = find_free_port(8765)

    ViewerHandler.manager = manager

    server_address = ("127.0.0.1", port)
    httpd = http.server.ThreadingHTTPServer(server_address, ViewerHandler)

    url = f"http://127.0.0.1:{port}/"
    print(f"agentcad view: {url}", flush=True)

    if open_browser:
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error):
            pass

    # Start server in background thread
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    return httpd, url


def serve(
    paths: list[str],
    *,
    port: int | None = None,
    open_browser: bool = True,
    bed: tuple[float, float] | None = None,
    colors: list[tuple[str, str, float]] = (),
    recursive: bool = False,
) -> None:
    """Start the viewer server and block until KeyboardInterrupt."""
    bed_list = list(bed) if bed else None
    manager = FileManager(paths, recursive=recursive, bed=bed_list, color_rules=list(colors))

    if port is None:
        port = find_free_port(8765)

    ViewerHandler.manager = manager

    server_address = ("127.0.0.1", port)
    httpd = http.server.ThreadingHTTPServer(server_address, ViewerHandler)

    url = f"http://127.0.0.1:{port}/"
    print(f"agentcad view: {url}", flush=True)

    if open_browser:
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error):
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentcad view",
        description="A simple, reusable local CAD mesh viewer for 3D printing and CAD parts.",
        epilog="""Examples:
  agentcad view ./stl --bed 350x350
  agentcad view part1.stl part2.obj -r ./parts --no-open --port 9000
  agentcad view *.stl --color shell=#c0392b --color diffuser=#ffffff@0.35
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="Mesh files or directories (.stl, .obj, .3mf, .glb, .gltf, .ply)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default: first free port from 8765 up, 127.0.0.1 only)")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically")
    parser.add_argument("--bed", type=str, default=None, help="Print bed outline dimensions WxD in mm (e.g. 350x350)")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan directories recursively for mesh files")

    parser.add_argument("--color", action="append", default=[], type=parse_color_rule, metavar="PATTERN=COLOR[@OPACITY]",
                        help="Repeatable. Colour files whose name contains PATTERN (case-insensitive, first match wins); "
                             "COLOR is #RRGGBB, OPACITY 0..1 makes it translucent (e.g. diffuser=#ffffff@0.35). Unmatched files keep the default palette")

    args = parser.parse_args(argv)

    bed_dims = parse_bed(args.bed)
    if isinstance(bed_dims, list):
        bed_tuple = (bed_dims[0], bed_dims[1])
    else:
        bed_tuple = None

    color_rules = list(args.color)

    serve(
        args.paths,
        port=args.port,
        open_browser=not args.no_open,
        bed=bed_tuple,
        colors=color_rules,
        recursive=args.recursive,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())