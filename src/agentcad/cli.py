"""Command line interface. Exit codes: 0 pass or warn, 1 fail, 2 usage or config error, 3 build error."""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import cadquery as cq
import trimesh

from . import __version__, measure, printability
from . import diff as diffmod
from .build import build, existing_build
from .checks import BUILTIN_IDS, run_checks
from .geometry import area, bbox, center_of_mass, is_valid, load, solids_count, to_mesh, volume
from .printers import PROFILES, Printer, load_user_profiles
from .project import BuildError, ConfigError, Project
from .report import Check, Report, Status, json_value
from .wall import wall_thickness

SCAFFOLD = Path(__file__).parent / "scaffold"
TEMPLATES = ("blank", "bracket", "box-with-lid")


class UsageError(ValueError):
    """Bad command line input that argparse could not catch (exit 2)."""


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``agentcad`` console script."""
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    try:
        return args.handler(args)
    except (ConfigError, UsageError) as exc:
        return _fail(args, exc, 2)
    except BuildError as exc:
        return _fail(args, exc, 3)


def _fail(args: argparse.Namespace, exc: Exception, code: int) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"schema": "agentcad.error/1", "error": str(exc), "exit_code": code}, indent=2))
    print(f"agentcad: error: {exc}", file=sys.stderr)
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentcad", description="CAD for AI agents: build parts, then measure them.")
    parser.add_argument("--version", action="version", version=f"agentcad {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, handler, help: str, json_flag: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help, description=help)
        p.set_defaults(handler=handler)
        if json_flag:
            p.add_argument("--json", action="store_true", help="emit one JSON document on stdout")
        return p

    p = add("new", _new, "create a project from a template")
    p.add_argument("dir", metavar="DIR")
    p.add_argument("--template", choices=TEMPLATES, default="blank")

    p = add("build", _build, "export STEP, STL and a measured manifest")
    p.add_argument("project", nargs="?", default=".", metavar="PROJECT")
    p.add_argument("--out", metavar="DIR", help="build folder (default PROJECT/build)")

    p = add("check", _check, "build, run every gate and the project's checks")
    p.add_argument("project", nargs="?", default=".", metavar="PROJECT")
    p.add_argument("--only", metavar="ID,ID", help=f"run only these check ids (built-in: {', '.join(BUILTIN_IDS)})")
    p.add_argument("--no-build", action="store_true", help="reuse build/ if it is fresh, else exit 2")
    p.add_argument("--brief", action="store_true",
                   help="only checks that need attention, with fix hints, plus totals (token-lean for agents)")

    p = add("measure", _measure, "bbox, volume, area, centre of mass of STEP or mesh files")
    p.add_argument("files", nargs="+", metavar="FILE")

    p = add("interfere", _interfere, "pairwise overlap between files as placed")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--tol", type=float, default=0.01, metavar="MM3", help="overlap allowed (default 0.01)")

    p = add("overhang", _overhang, "unsupported downward faces of files as placed (print frame, bed at z=0)")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--max-deg", type=float, default=45, help="tilt from vertical that needs support (default 45)")
    p.add_argument("--allow", type=float, default=25, metavar="MM2", help="unsupported area allowed (default 25)")
    p.add_argument("--first-layer", type=float, default=0.3, metavar="MM",
                   help="ignore downward faces centred below this height, such as a bottom chamfer (default 0.3)")

    p = add("wall", _wall, "ray-cast wall thickness (port of stlwall)")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--samples", type=int, default=60000)
    p.add_argument("--thin", type=float, default=1.9, metavar="MM", help="report the share under this (default 1.9)")
    p.add_argument("--bands", type=float, default=0.0, metavar="MM", help="also tabulate per z band of this height")
    p.add_argument("--up", action="store_true", help="only faces pointing up (nz > 0.3)")
    for flag in ("zmin", "zmax", "rmin", "rmax"):
        p.add_argument(f"--{flag}", type=float)

    p = add("fit", _fit, "does each file fit a printer bed?")
    p.add_argument("files", nargs="+", metavar="FILE")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--printer", metavar="NAME")
    group.add_argument("--bed", metavar="XxYxZ", help="bed size in mm, for example 220x220x250")

    p = add("render", _render, "render PNG views of a project or files")
    p.add_argument("inputs", nargs="*", metavar="PROJECT|FILE")
    p.add_argument("-o", "--out", metavar="DIR", help="default PROJECT/build/renders, or ./renders for files")
    p.add_argument("--frame", choices=("use", "print"), default="use")
    p.add_argument("--views", default="iso,front,right,top")
    p.add_argument("--section", metavar="AXIS=MM", help="cut with a plane, for example z=10")

    p = add("view", _view, "open the live 3D viewer")
    p.add_argument("inputs", nargs="*", metavar="PROJECT|PATH")
    p.add_argument("--port", type=int)
    p.add_argument("--no-open", action="store_true", help="do not open a browser")
    p.add_argument("--bed", metavar="WxD", help="draw a bed outline, for example 220x220")
    p.add_argument("--color", action="append", default=[], metavar="PATTERN=#RRGGBB[@OPACITY]")
    p.add_argument("-r", "--recursive", action="store_true")

    p = add("diff", _diff, "compare two builds")
    p.add_argument("old", metavar="OLD", help="manifest.json or build folder")
    p.add_argument("new", metavar="NEW", help="manifest.json or build folder")

    add("printers", _printers, "list printer profiles")
    add("doctor", _doctor, "check versions, offscreen rendering and write access")

    # -- drawing (owned by drawing.py) --
    p = add("drawing", _drawing, "dimensioned 2D drawings (SVG, PDF, PNG) from the measured geometry")
    p.add_argument("inputs", nargs="*", metavar="PROJECT|FILE", help="a project folder or STEP files (default .)")
    p.add_argument("-o", "--out", metavar="DIR", help="default PROJECT/build/drawings, or ./drawings for files")
    p.add_argument("--parts", metavar="NAME,NAME", help="only these parts of a project")
    p.add_argument("--format", default="svg,pdf,png", metavar="FMT,FMT", help="any of svg, pdf, png")
    p.add_argument("--projection", choices=("third", "first"), default="third",
                   help="third-angle (US, default) or first-angle (ISO/EU) view layout")
    p.add_argument("--sheet", choices=("auto", "A4", "A3", "letter", "tabloid"), default="auto")
    # -- end drawing --

    # -- api (owned by api.py) --
    add("api", _api, "everything an agent needs in one document: commands, ctx helpers, config keys, gates")
    # -- end api --

    # -- skill (owned by skill_install.py) --
    p = add("skill", _skill, "install, show or locate the agentcad agent skill")
    p.add_argument("action", choices=("install", "show", "path"))
    p.add_argument("--host", choices=("claude", "codex", "cursor", "agents-md"), default="claude",
                   help="which agent to install for (default claude)")
    p.add_argument("--project", metavar="DIR", help="install into this project instead of your home folder")
    p.add_argument("--link", action="store_true", help="symlink instead of copying (stays current with this install)")
    p.add_argument("--force", action="store_true", help="replace a different existing install")
    p.add_argument("--reference", metavar="NAME", help="with show: print one reference file instead of SKILL.md")
    # -- end skill --

    # -- mcp (owned by mcp_server.py) --
    p = add("mcp", _mcp, "run the MCP server on stdio (needs the 'mcp' extra)", json_flag=False)
    p.add_argument("--root", metavar="DIR", help="refuse paths outside this folder")
    # -- end mcp --
    return parser


def _drawing(args: argparse.Namespace) -> int:
    from .drawing import cli

    return cli(args)


def _api(args: argparse.Namespace) -> int:
    from .api import cli

    return cli(args)


def _skill(args: argparse.Namespace) -> int:
    from .skill_install import cli

    return cli(args)


def _mcp(args: argparse.Namespace) -> int:
    from .mcp_server import cli

    return cli(args)


def _emit(args: argparse.Namespace, report: Report, extra: Sequence[str] = ()) -> int:
    brief = getattr(args, "brief", False)
    if args.json:
        print(report.to_json(view="brief" if brief else "full"))
    elif brief:
        print(report.to_brief_table())
    else:
        lines = report.to_table().splitlines()
        # Extra detail lines (wall bands) sit under the per-file rows, above the totals line.
        print("\n".join([*lines[:-1], *extra, lines[-1]]))
    return report.exit_code()


def _is_project(text: str) -> bool:
    path = Path(text)
    return (path / "agentcad.toml").is_file() or (path.is_file() and path.name == "agentcad.toml")


def _load_files(paths: Sequence[str]) -> list[tuple[str, object]]:
    try:
        return [(Path(p).name, load(p)) for p in paths]
    except (ValueError, OSError) as exc:
        raise UsageError(str(exc)) from exc
    except Exception as exc:  # unreadable or corrupt geometry
        raise UsageError(f"Cannot read geometry: {type(exc).__name__}: {exc}") from exc


def _as_mesh(geometry: object) -> trimesh.Trimesh:
    return to_mesh(geometry) if isinstance(geometry, cq.Shape) else geometry


# -- project commands ---------------------------------------------------------------------------------------------

def _new(args: argparse.Namespace) -> int:
    target = Path(args.dir)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise UsageError(f"{target} exists and is not empty")
    shutil.copytree(SCAFFOLD / args.template, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    _name_project(target / "agentcad.toml", target.resolve().name)
    files = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    if args.json:
        print(json.dumps({"schema": "agentcad.new/1", "path": str(target.resolve()), "template": args.template,
                          "files": files}, indent=2))
    else:
        print(f"created {target} from template '{args.template}': {', '.join(files)}")
        print(f"next: cd {target} && agentcad check")
    return 0


def _name_project(config: Path, name: str) -> None:
    """Replace the template's placeholder project.name with the folder name, so renders and reports are not mislabelled."""
    if not config.is_file():
        return
    text = config.read_text()
    # json.dumps gives a valid TOML basic string for any folder name (quotes, backslashes, unicode).
    renamed = re.sub(r'(?m)^name\s*=\s*"[^"\n]*"', lambda _: f"name = {json.dumps(name)}", text, count=1)
    config.write_text(renamed)


def _build(args: argparse.Namespace) -> int:
    result = build(args.project, args.out)
    if args.json:
        print(json.dumps(result.manifest, indent=2))
        return 0
    for part in result.manifest["parts"]:
        size = " x ".join(f"{v:.1f}" for v in part["bbox"]["print"]["size_mm"])
        print(f"{part['name']}: {part['volume_mm3']:.1f} mm3, print {size} mm, {part['triangles']} triangles")
    print(f"built {len(result.manifest['parts'])} part(s) into {result.path}")
    return 0


def _hard_timeout_s() -> float:
    """Seconds after which a still-running check is killed with a stack dump (AGENTCAD_CHECK_TIMEOUT_S, default 1800)."""
    try:
        value = float(os.environ.get("AGENTCAD_CHECK_TIMEOUT_S", "1800"))
    except ValueError:
        value = 1800.0
    return value if value > 0 else 1e9


def _arm_backstop() -> bool:
    """Start the hard timer; False when no real stderr is available (some embedders replace sys.stderr)."""
    for stream in (sys.stderr, sys.__stderr__):
        try:
            faulthandler.dump_traceback_later(_hard_timeout_s(), exit=True, file=stream)
            return True
        except (OSError, ValueError, AttributeError):  # io.UnsupportedOperation is an OSError
            continue
    return False


def _check(args: argparse.Namespace) -> int:
    only = [i.strip() for i in args.only.split(",") if i.strip()] if args.only else None
    path = Path(args.project)
    build_started = time.perf_counter()
    try:
        project = Project.load(path)
        result = existing_build(project) if args.no_build else build(project)
        build_s = time.perf_counter() - build_started
    except BuildError as exc:
        # The model code raised or produced no valid solid: report it as an ERROR check, exit 3.
        name = path.resolve().name if path.is_dir() else path.resolve().parent.name
        return _emit(args, Report("check", name, [Check("valid", None, Status.ERROR, str(exc))],
                                  {"agentcad": __version__}))
    # OCC booleans cannot be interrupted from Python, so a truly endless one is only stopped by this C-level timer,
    # which prints the Python stack (the project check line that was running) and exits.
    armed = _arm_backstop()
    try:
        report = run_checks(project, result, only, build_s=build_s)
    finally:
        if armed:
            faulthandler.cancel_dump_traceback_later()
    if only and not report.checks:
        raise UsageError(f"--only {args.only!r} matched no check")
    (result.path / "report.json").write_text(report.to_json() + "\n")
    return _emit(args, report)


# -- file commands ------------------------------------------------------------------------------------------------

def _measure(args: argparse.Namespace) -> int:
    checks = []
    for name, geometry in _load_files(args.files):
        box = bbox(geometry)
        ok = is_valid(geometry)
        measured = {"bbox": box, "volume_mm3": volume(geometry), "area_mm2": area(geometry),
                    "solids": solids_count(geometry), "valid": ok}
        if ok:
            measured["center_of_mass_mm"] = center_of_mass(geometry)
        size = " x ".join(f"{v:.2f}" for v in box["size_mm"])
        status = Status.PASS if ok else Status.WARN
        note = "" if ok else "; not a closed solid, volume is unreliable"
        checks.append(Check("measure", name, status,
                            f"{size} mm, {measured['volume_mm3']:.1f} mm3, {measured['area_mm2']:.1f} mm2{note}",
                            measured))
    return _emit(args, Report("measure", None, checks, {"agentcad": __version__}))


def _interfere(args: argparse.Namespace) -> int:
    if len(args.files) < 2:
        raise UsageError("interfere needs at least two files")
    items = _load_files(args.files)
    checks = []
    for i, (name_a, a) in enumerate(items):
        for name_b, b in items[i + 1:]:
            overlap = measure.interference(a, b)
            status = Status.PASS if overlap <= args.tol else Status.FAIL
            checks.append(Check("interference", f"{name_a}+{name_b}", status, f"overlap {overlap:.3f} mm3",
                                {"overlap_mm3": overlap}, {"max_overlap_mm3": args.tol}))
    return _emit(args, Report("interfere", None, checks, {"agentcad": __version__}))


def _overhang(args: argparse.Namespace) -> int:
    checks = []
    for name, geometry in _load_files(args.files):
        result = printability.overhang(_as_mesh(geometry), args.max_deg, first_layer_mm=args.first_layer)
        ok = result["area_mm2"] <= args.allow
        where = [{"centroid_mm": r["centroid_mm"], "area_mm2": r["area_mm2"], "z_mm": r["z_mm"]}
                 for r in result["regions"]]
        checks.append(Check("overhang", name, Status.PASS if ok else Status.FAIL,
                            f"{result['area_mm2']:.1f} mm2 unsupported at {args.max_deg:g} deg (allowed {args.allow:g})",
                            {"area_mm2": result["area_mm2"], "regions": result["region_count"],
                             "first_layer_area_mm2": result["first_layer_area_mm2"]},
                            {"max_area_mm2": args.allow, "max_overhang_deg": args.max_deg,
                             "first_layer_mm": args.first_layer}, where))
    return _emit(args, Report("overhang", None, checks, {"agentcad": __version__}))


def _wall(args: argparse.Namespace) -> int:
    checks, extra = [], []
    for name, geometry in _load_files(args.files):
        r = wall_thickness(_as_mesh(geometry), args.samples, 0, args.thin, args.zmin, args.zmax, bands=args.bands,
                           up=args.up, rmin=args.rmin, rmax=args.rmax)
        if not r["hits"]:
            checks.append(Check("wall", name, Status.WARN, "no hits (open mesh or empty selection)", r))
            continue
        under = f"under {args.thin:g}: {100 * r['thin_share']:.2f}% of surface"
        if r["thin_z_mm"]:
            under += f" (z {r['thin_z_mm'][0]:.0f}..{r['thin_z_mm'][1]:.0f})"
        checks.append(Check("wall", name, Status.PASS,
                            f"{r['hits']}/{r['samples']} hits, min {r['min_mm']:.2f} p1 {r['p1_mm']:.2f} "
                            f"p5 {r['p5_mm']:.2f} median {r['median_mm']:.2f} mm, {under}", r))
        for band in r["bands"]:
            low, high = band["z_mm"]
            extra.append(f"  {name} z {low:6.1f}-{high:6.1f}: min {band['min_mm']:.2f} p2 {band['p2_mm']:.2f} "
                         f"median {band['median_mm']:.2f}  (n={band['samples']})")
    return _emit(args, Report("wall", None, checks, {"agentcad": __version__, "samples": args.samples, "seed": 0}),
                 extra)


def _fit(args: argparse.Namespace) -> int:
    if args.bed:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)", args.bed)
        if not match:
            raise UsageError("--bed must look like 220x220x250")
        printer = Printer("custom", tuple(float(v) for v in match.groups()))
    else:
        profiles = {**PROFILES, **load_user_profiles()}
        if args.printer not in profiles:
            raise UsageError(f"Unknown printer {args.printer!r}; available: {', '.join(sorted(profiles))}")
        printer = profiles[args.printer]
    bed = "x".join(f"{v:g}" for v in printer.bed_xyz)
    checks = []
    for name, geometry in _load_files(args.files):
        fit = printability.bed_fit(geometry, printer.bed_xyz)
        x, y, z = fit["size_mm"]
        text = f"{x:.1f} x {y:.1f} x {z:.1f} mm {'fits' if fit['fits'] else 'does not fit'} {printer.name} {bed}"
        if fit["fits"] and fit["rotation_z_deg"]:
            text += " (rotate 90 deg about Z)"
        checks.append(Check("bed_fit", name, Status.PASS if fit["fits"] else Status.FAIL, text, fit,
                            {"bed_mm": list(printer.bed_xyz), "margin_mm": fit["margin_mm"]}))
    return _emit(args, Report("fit", None, checks, {"agentcad": __version__, "printer": printer.name}))


# -- visual commands ----------------------------------------------------------------------------------------------

def _project_meshes(project_path: str, frame: str) -> tuple[Project, list[tuple[str, trimesh.Trimesh, str | None]]]:
    project = Project.load(project_path)
    result = build(project)
    folder, items, offset = ("assembly" if frame == "use" else "stl"), [], 0.0
    for name, part in project.parts.items():
        mesh = trimesh.load(str(result.path / folder / f"{name}.stl"), force="mesh")
        if frame == "print":
            # Print-frame parts all sit at the origin; lay them out in a row so the sheet stays readable.
            mesh = mesh.copy()
            mesh.apply_translation((offset + mesh.extents[0] / 2, 0, 0))
            offset += mesh.extents[0] + 10
        items.append((name, mesh, part.color))
    return project, items


def _render(args: argparse.Namespace) -> int:
    try:
        from .render import offscreen_ok, render_sheet
    except ImportError as exc:
        raise UsageError(f"render is unavailable: {exc}") from exc
    inputs = args.inputs or ["."]
    section = None
    if args.section:
        match = re.fullmatch(r"([xyzXYZ])=(-?\d+(?:\.\d+)?)", args.section)
        if not match:
            raise UsageError("--section must look like z=10")
        section = (match.group(1).lower(), float(match.group(2)))
    views = tuple(v.strip() for v in args.views.split(",") if v.strip())
    ok, message = offscreen_ok()
    if not ok:
        raise UsageError(f"offscreen rendering is unavailable ({message}); run 'agentcad doctor'")
    if len(inputs) == 1 and _is_project(inputs[0]):
        project, items = _project_meshes(inputs[0], args.frame)
        out, title = args.out or str(project.path / "build" / "renders"), project.name
    else:
        loaded = _load_files(inputs)
        items = [(Path(p).stem, _as_mesh(g), None) for p, (_, g) in zip(inputs, loaded)]
        out, title = args.out or "renders", None
    files = render_sheet(items, out, views=views, section=section, title=title)
    if args.json:
        print(json.dumps({"schema": "agentcad.render/1", "out": str(Path(out).resolve()), "files": files}, indent=2))
    else:
        print("\n".join(files))
    return 0


def _view(args: argparse.Namespace) -> int:
    try:
        from .viewer.server import NoMeshFilesError, PortInUseError, serve
    except ImportError as exc:
        raise UsageError(f"view is unavailable: {exc}") from exc
    inputs = args.inputs or ["."]
    bed, colors = None, []
    for text in args.color:
        match = re.fullmatch(r"(.+)=(#[0-9a-fA-F]{6})(?:@(\d*\.?\d+))?", text)
        if not match:
            raise UsageError(f"--color must look like PATTERN=#RRGGBB[@OPACITY], got {text!r}")
        colors.append((match.group(1).lower(), match.group(2).lower(), float(match.group(3) or 1)))
    if args.bed:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)", args.bed)
        if not match:
            raise UsageError("--bed must look like 220x220")
        bed = (float(match.group(1)), float(match.group(2)))
    if len(inputs) == 1 and _is_project(inputs[0]):
        project = Project.load(inputs[0])
        result = build(project)
        paths = [str(result.path / "assembly" / f"{name}.stl") for name in project.parts]
        colors += [(f"{name}.stl", p.color, 1.0) for name, p in project.parts.items() if p.color]
        bed = bed or tuple(project.printer.bed_xyz[:2])
    else:
        paths = inputs
    try:
        serve(paths, port=args.port, open_browser=not args.no_open, bed=bed, colors=colors, recursive=args.recursive)
    except (PortInUseError, NoMeshFilesError) as exc:
        raise UsageError(str(exc)) from exc
    return 0


# -- reporting commands -------------------------------------------------------------------------------------------

def _diff(args: argparse.Namespace) -> int:
    try:
        document = diffmod.diff_manifests(diffmod.load_manifest(args.old), diffmod.load_manifest(args.new))
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    print(json.dumps(document, indent=2) if args.json else diffmod.to_text(document))
    return 0


def _printers(args: argparse.Namespace) -> int:
    try:
        profiles = {**PROFILES, **load_user_profiles()}
    except ValueError as exc:
        raise ConfigError(f"printers.toml: {exc}") from exc
    if args.json:
        rows = [{"name": p.name, "bed_mm": list(p.bed_xyz), "nozzle_mm": p.nozzle_mm, "notes": p.notes}
                for p in profiles.values()]
        print(json.dumps(json_value({"schema": "agentcad.printers/1", "printers": rows}), indent=2))
    else:
        for p in profiles.values():
            print(f"{p.name:<14} {'x'.join(f'{v:g}' for v in p.bed_xyz):<12} nozzle {p.nozzle_mm:g} mm")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    checks = [Check("python", None, Status.PASS, f"Python {platform.python_version()} on {platform.system()}"),
              Check("agentcad", None, Status.PASS, f"agentcad {__version__}"),
              Check("cadquery", None, Status.PASS, f"CadQuery {cq.__version__}, trimesh {trimesh.__version__}")]
    try:
        from .render import offscreen_ok

        ok, message = offscreen_ok()
        checks.append(Check("offscreen", None, Status.PASS if ok else Status.WARN,
                            message or ("offscreen rendering works" if ok else "offscreen rendering failed")))
    except ImportError as exc:
        checks.append(Check("offscreen", None, Status.WARN, f"render module unavailable: {exc}"))
    try:
        with tempfile.NamedTemporaryFile(dir=Path.cwd()):
            pass
        checks.append(Check("write_access", None, Status.PASS, f"can write to {Path.cwd()}"))
    except OSError as exc:
        checks.append(Check("write_access", None, Status.FAIL, f"cannot write to {Path.cwd()}: {exc.strerror}"))
    try:
        count = len(load_user_profiles())
        checks.append(Check("printers", None, Status.PASS, f"{len(PROFILES)} built-in, {count} user profile(s)"))
    except ValueError as exc:
        checks.append(Check("printers", None, Status.FAIL, f"~/.config/agentcad/printers.toml: {exc}"))
    started = time.perf_counter()
    return _emit(args, Report("doctor", None, checks, {"agentcad": __version__, "cwd": os.getcwd(),
                                                       "elapsed_s": round(time.perf_counter() - started, 2)}))


if __name__ == "__main__":
    sys.exit(main())
