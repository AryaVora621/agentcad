"""The built-in gates behind ``agentcad check`` and the context handed to project checks."""
from __future__ import annotations

import os
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from itertools import combinations

import cadquery as cq
import numpy as np
import trimesh

from . import __version__, measure, printability
from .flat import FLAT_IDS, flat_gates
from .geometry import is_valid, solids_count, to_shape, volume
from .project import BuildResult, Project
from .report import Check, Report, Status
from .wall import wall_thickness

PRINT_IDS = ("valid", "single_solid", "watertight", "bed_fit", "bed_contact", "overhang", "min_wall",
             "interference")
BUILTIN_IDS = PRINT_IDS + FLAT_IDS
WALL_SAMPLES = 40000
# bed_contact warns below this share of the footprint or this absolute area (mm2).
MIN_CONTACT_SHARE, MIN_CONTACT_MM2 = 0.01, 20.0
# One project check function normally needs seconds; this stops a search loop of slow booleans on a shared machine.
DEFAULT_CHECK_BUDGET_S = 300.0

PartRef = str | cq.Shape | cq.Workplane


class CheckContext:
    """What a project's ``checks(ctx)`` measures with. Every part argument is a part name or a probe solid.

    A probe is a ``cq.Shape`` or ``cq.Workplane`` already in the use frame, for example a screw stand-in built
    from the requirement. Results are measured on the built solids, never read back from parameters.
    """

    def __init__(self, project: Project, build_result: BuildResult) -> None:
        self.project = project
        self.build = build_result
        self.checks: list[Check] = []
        self._cache: dict[tuple[str, str], cq.Shape] = {}

    def part(self, name: str):
        """Return the Part called ``name``."""
        if name not in self.project.parts:
            raise ValueError(f"Unknown part {name!r}; parts: {', '.join(self.project.parts)}")
        return self.project.parts[name]

    def solid(self, name: str, frame: str = "use") -> cq.Shape:
        """Return the built solid of a part in a frame ("use" by default)."""
        key = (name, frame)
        if key not in self._cache:
            self._cache[key] = self.part(name).solid(frame)
        return self._cache[key]

    def _shape(self, ref: PartRef) -> cq.Shape:
        if isinstance(ref, str):
            return self.solid(ref, "use")
        if isinstance(ref, (cq.Shape, cq.Workplane)):
            return to_shape(ref)
        raise TypeError(f"Expected a part name, cq.Shape or cq.Workplane, got {type(ref).__name__}")

    def _shapes(self, refs: PartRef | Sequence[PartRef]) -> list[cq.Shape]:
        if isinstance(refs, (list, tuple)):
            return [self._shape(r) for r in refs]
        return [self._shape(refs)]

    def interference(self, a: PartRef, b: PartRef) -> float:
        """Overlap volume of two parts in the use frame, mm3."""
        return measure.interference(self._shape(a), self._shape(b))

    def distance(self, a: PartRef, b: PartRef) -> float:
        """Minimum distance between two parts in the use frame, mm (0 if they touch or overlap)."""
        return measure.distance(self._shape(a), self._shape(b))

    def section(self, names: PartRef | Sequence[PartRef], axis: str = "z", at: float = 0.0) -> dict:
        """Planar cut of one or more parts: ``{"area_mm2", "bbox_mm", "loops"}``."""
        return measure.section(self._shapes(names), axis, at)

    def slide(self, moving: PartRef, against: PartRef | Sequence[PartRef], direction: Sequence[float],
              distance: float, steps: int = 40) -> list[dict]:
        """Overlap of ``moving`` with ``against`` at ``steps + 1`` offsets up to ``distance`` mm along ``direction``."""
        return measure.slide(self._shape(moving), self._shapes(against), direction, distance, steps)

    def play(self, moving: PartRef, against: PartRef | Sequence[PartRef], direction: Sequence[float],
             max_mm: float = 5.0, resolution_mm: float = 0.01) -> float | None:
        """Free travel in mm before ``moving`` overlaps ``against``; None if it never touches within ``max_mm``.

        A probe that already overlaps at its start returns 0.0 with ``starts_in_contact`` set, and ``expect``
        says so in its summary.
        """
        return measure.play(self._shape(moving), self._shapes(against), direction, max_mm, resolution_mm,
                            self.project.checks["interference_tol_mm3"])

    def expect(self, id: str, value: float, *, min: float | None = None, max: float | None = None,
               part: str | None = None, unit: str = "mm", summary: str | None = None,
               hint: str | None = None, where: list[dict] | None = None) -> Check:
        """Record PASS when ``min <= value <= max`` and FAIL otherwise. At least one bound is required.

        ``hint`` says how to fix a failure; it is only attached when the check fails. ``where`` lists the
        locations to look at (use frame unless an entry says otherwise).
        """
        if min is None and max is None:
            raise ValueError(f"expect({id!r}): give min, max or both")
        if value is None:
            raise ValueError(f"expect({id!r}): value is None; the measurement found nothing "
                             "(for example play() never touched within max_mm)")
        ok = (min is None or value >= min) and (max is None or value <= max)
        limit = {f"{key}_{unit}": bound for key, bound in (("min", min), ("max", max)) if bound is not None}
        text = " and ".join(f"{key} {bound:g}" for key, bound in (("min", min), ("max", max)) if bound is not None)
        shown = f"{value:.4g} {unit}"
        if summary is None:
            summary = f"{shown} ({'within' if ok else 'outside'} {text})"
        elif f"{value:.4g}" not in summary:
            # A custom label alone hides the number an agent must quote.
            summary = f"{summary}: {shown}"
        measured = {f"value_{unit}": value}
        if getattr(value, "starts_in_contact", False):
            # play() returned 0 because the probe overlaps at the start, not because it measured a gap of 0.
            summary += "; probe starts in contact (already overlapping at its start position)"
            measured["starts_in_contact"] = True
        return self.record(Check(id, part, Status.PASS if ok else Status.FAIL, summary, measured, limit,
                                 list(where or []), hint=None if ok else hint))

    def record(self, check: Check) -> Check:
        """Add a hand-built Check to the report.

        Project checks measure in the use frame, so a ``where`` entry without a frame is tagged ``"use"``.
        """
        for entry in check.where:
            if isinstance(entry, dict):
                entry.setdefault("frame", "use")
        self.checks.append(check)
        return check


def run_checks(project: Project, result: BuildResult, only: Iterable[str] | None = None,
               build_s: float | None = None) -> Report:
    """Run the built-in gates, then the project's own checks, and return one Report.

    ``build_s`` is the time the caller spent building; ``elapsed_s`` only covers the gates, so without both a reader
    cannot tell a rebuild from a skipped one.
    """
    started = time.perf_counter()
    wanted = set(only) if only is not None else None
    ctx = CheckContext(project, result)
    checks = _builtin(ctx, wanted)
    if wanted is None or wanted - set(BUILTIN_IDS):
        checks += [c for c in _project_checks(ctx) if wanted is None or c.id in wanted]
    meta = {"agentcad": __version__, "cadquery": cq.__version__, "printer": project.printer.name,
            "bed_mm": list(project.printer.bed_xyz), "frames": {"printability": "print", "fit": "use"},
            "build": str(result.path), "elapsed_s": round(time.perf_counter() - started, 2)}
    if build_s is not None:
        meta["build_s"] = round(build_s, 2)
    return Report("check", project.name, checks, meta)


def _guard(id: str, part: str | None, gate: Callable[[], list[Check]]) -> list[Check]:
    try:
        return gate()
    except Exception as exc:  # noqa: BLE001  a broken gate must not hide the other results
        return [Check(id, part, Status.ERROR, f"{type(exc).__name__}: {exc}")]


def _builtin(ctx: CheckContext, wanted: set[str] | None) -> list[Check]:
    project = ctx.project
    cfg = project.checks
    out: list[Check] = []

    def on(id: str) -> bool:
        return wanted is None or id in wanted

    parts = list(project.parts.values())
    # Cut (laser, CNC, waterjet) parts get the flat gates instead of the 3D printing gates.
    printed = [p for p in parts if p.printed and getattr(p, "cut", None) is None]
    if on("valid"):
        out += [c for p in parts for c in _guard("valid", p.name, lambda p=p: [_valid(p.name, ctx.solid(p.name, "model"))])]
    if on("single_solid"):
        out += [c for p in parts for c in
                _guard("single_solid", p.name, lambda p=p: [_single_solid(p.name, ctx.solid(p.name, "model"))])]
    for gate_id, gate in (("watertight", lambda p: _watertight(p.name, ctx.build.meshes[p.name])),
                          ("bed_fit", lambda p: _bed_fit(p.name, ctx.build.meshes[p.name], project.printer)),
                          ("bed_contact", lambda p: _bed_contact(p.name, ctx.build.meshes[p.name])),
                          ("overhang", lambda p: _overhang(p.name, ctx.build.meshes[p.name], cfg))):
        if on(gate_id):
            out += [c for p in printed for c in _guard(gate_id, p.name, lambda p=p, gate=gate: [gate(p)])]
    if on("min_wall"):
        if cfg["min_wall_mm"] > 0:
            out += [c for p in printed for c in
                    _guard("min_wall", p.name, lambda p=p: [_min_wall(p.name, ctx.build.meshes[p.name], cfg)])]
        else:
            out.append(Check("min_wall", None, Status.SKIP, "disabled; set checks.min_wall_mm to enable"))
    if on("interference"):
        allowed = {frozenset(pair) for pair in cfg["allow_overlap"]}
        for a, b in combinations(parts, 2):
            out += _guard("interference", f"{a.name}+{b.name}", lambda a=a, b=b: [
                _interference(a.name, b.name, ctx.solid(a.name), ctx.solid(b.name),
                              cfg["interference_tol_mm3"], frozenset((a.name, b.name)) in allowed)])
    out += flat_gates(ctx, wanted)
    return out


def check_budget_s() -> float | None:
    """Soft time budget per project check function, seconds; ``AGENTCAD_CHECK_BUDGET_S`` overrides, 0 disables."""
    try:
        value = float(os.environ.get("AGENTCAD_CHECK_BUDGET_S", DEFAULT_CHECK_BUDGET_S))
    except ValueError:
        value = DEFAULT_CHECK_BUDGET_S
    return value if value > 0 else None


def _project_checks(ctx: CheckContext) -> list[Check]:
    module = ctx.project.module
    functions: list[tuple[str, Callable]] = []
    if callable(getattr(module, "checks", None)):
        functions.append(("checks", module.checks))
    # Extra isolated entry points: one failing check_* function does not stop the others.
    functions += [(name, fn) for name, fn in vars(module).items()
                  if name.startswith("check_") and callable(fn) and getattr(fn, "__module__", None) == module.__name__]
    budget = check_budget_s()
    with ctx.project.on_path():
        for name, function in functions:
            try:
                with measure.time_limit(budget, f"project check {name}()"):
                    function(ctx)
            except Exception as exc:  # noqa: BLE001  project code may raise anything
                last = traceback.format_exception_only(type(exc), exc)[-1].strip()
                ctx.record(Check(name, None, Status.ERROR, f"raised {last}"))
    return ctx.checks


def _valid(name: str, shape: cq.Shape) -> Check:
    solids, vol = solids_count(shape), volume(shape)
    measured = {"valid": bool(shape.isValid()), "solids": solids, "volume_mm3": vol}
    if is_valid(shape):
        return Check("valid", name, Status.PASS, f"valid solid, {vol:.1f} mm3", measured)
    return Check("valid", name, Status.FAIL, f"not a valid positive-volume solid (solids {solids}, volume {vol:.1f} mm3)",
                 measured, {"min_volume_mm3": 0},
                 hint=(f"the shape has {solids} solid(s) and {vol:.1f} mm3: look for a boolean (cut, intersect, shell, "
                       "fillet) that emptied it or left an open shell, and build the part from closed solids"))


def _single_solid(name: str, shape: cq.Shape) -> Check:
    n = solids_count(shape)
    if n <= 1:
        return Check("single_solid", name, Status.PASS, "one solid", {"solids": n})
    return Check("single_solid", name, Status.WARN, f"{n} disconnected solids; one print, or split into parts?",
                 {"solids": n}, {"max_solids": 1},
                 hint=(f"join the {n} islands with a bridging feature (union a rib or plate) if it is one print, "
                       "or declare each island as its own Part"))


def _watertight(name: str, mesh) -> Check:
    tight, consistent = bool(mesh.is_watertight), bool(mesh.is_winding_consistent)
    measured = {"watertight": tight, "winding_consistent": consistent, "triangles": len(mesh.faces)}
    if tight and consistent:
        return Check("watertight", name, Status.PASS, f"watertight, {len(mesh.faces)} triangles", measured)
    reason = "has open edges" if not tight else "has inconsistent winding"
    where = open_edge_clusters(mesh)
    measured["open_edges"] = where.total
    return Check("watertight", name, Status.FAIL, f"print STL {reason}; slicers may repair or reject it", measured,
                 where=list(where),
                 hint=("remove zero-thickness faces, sliver cuts and coincident faces from the model "
                       "(offset a cut by 0.01 mm past the surface) so the print STL closes; the open edges are in "
                       "where (print frame). Revolve axes, sphere poles and tangent seams also open the mesh: rotate "
                       "the revolve axis off the seam, split the sphere into two hemispheres, or union a small overlap"))


class _Clusters(list):
    """The cluster list plus the total open-edge count, so callers can report both."""

    total = 0


def open_edge_clusters(mesh, limit: int = 10) -> _Clusters:
    """Group the mesh's open or non-manifold edges into connected clusters, largest first.

    A closed surface uses every edge exactly twice, so edges used any other number of times are where the
    mesh leaks. Each cluster is reported with its centre and extent in the print frame.
    """
    edges = mesh.edges_sorted
    if not len(edges):
        return _Clusters()
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    bad = unique[counts != 2]
    out = _Clusters()
    out.total = len(bad)
    if not len(bad):
        return out
    components = trimesh.graph.connected_components(bad, nodes=np.unique(bad))
    label = {int(node): index for index, nodes in enumerate(components) for node in nodes}
    sizes = np.bincount([label[int(a)] for a, _ in bad], minlength=len(components))
    vertices = np.asarray(mesh.vertices)
    ranked = []
    for index, nodes in enumerate(components):
        points = vertices[np.asarray(nodes, dtype=int)]
        low, high = points.min(axis=0), points.max(axis=0)
        ranked.append((int(sizes[index]), {"centroid_mm": ((low + high) / 2).tolist(),
                                           "extent_mm": (high - low).tolist(), "edges": int(sizes[index]),
                                           "frame": "print"}))
    ranked.sort(key=lambda item: item[0], reverse=True)
    out.extend(entry for _, entry in ranked[:limit])
    return out


def _bed_fit(name: str, mesh, printer) -> Check:
    fit = printability.bed_fit(mesh, printer.bed_xyz)
    x, y, z = fit["size_mm"]
    bed = "x".join(f"{v:g}" for v in printer.bed_xyz)
    limit = {"bed_mm": list(printer.bed_xyz), "margin_mm": fit["margin_mm"]}
    if fit["fits"]:
        turn = " (rotate 90 deg about Z)" if fit["rotation_z_deg"] else ""
        return Check("bed_fit", name, Status.PASS, f"{x:.1f} x {y:.1f} x {z:.1f} mm fits {printer.name} {bed}{turn}",
                     fit, limit)
    return Check("bed_fit", name, Status.FAIL,
                 f"{x:.1f} x {y:.1f} x {z:.1f} mm does not fit {printer.name} {bed} with {fit['margin_mm']:g} mm margin",
                 fit, limit, hint=_bed_fit_hint(fit["size_mm"], printer, fit["margin_mm"]))


def _bed_fit_hint(size: list[float], printer, margin: float) -> str:
    """Name the worst overshoot and the cheapest fixes, assuming the part is placed in its better XY orientation."""
    x, y, z = size
    bx, by, bz = printer.bed_xyz
    bed = " x ".join(f"{v:g}" for v in printer.bed_xyz)
    ax, ay = bx - 2 * margin, by - 2 * margin
    direct, swapped = (x - ax, y - ay), (y - ax, x - ay)
    over_x, over_y = min(direct, swapped, key=max)
    axes = sorted([(over_x, "long", "X"), (over_y, "long", "Y"), (z - bz, "tall", "Z")], reverse=True)
    amount, word, axis = axes[0]
    options = []
    if axes[0][2] == "Z":
        options.append("lay it on its side with Part.print (rx=90 or ry=90)")
    else:
        # A part turned 45 degrees spans (x + y) / sqrt(2) on both axes; only offer it when that actually fits.
        if (x + y) / 2 ** 0.5 <= min(ax, ay) and z <= bz:
            options.append("rotate it in Part.print (rz=45 reaches the bed diagonal)")
        else:
            options.append("rotate it in Part.print (rz) to use the bed diagonal or lay it on its side (rx/ry=90)")
    options += ["split it into parts", "or set [printer] to the real machine"]
    return f"{amount:.1f} mm too {word} in {axis} for {printer.name} ({bed}): " + ", ".join(options)


def _bed_contact(name: str, mesh) -> Check:
    contact = printability.bed_contact(mesh)
    low, high = mesh.bounds
    footprint = float((high[0] - low[0]) * (high[1] - low[1]))
    share = contact / footprint if footprint else 0.0
    measured = {"contact_mm2": contact, "footprint_mm2": footprint, "share": share}
    limit = {"min_share": MIN_CONTACT_SHARE, "min_contact_mm2": MIN_CONTACT_MM2}
    if share < MIN_CONTACT_SHARE or contact < MIN_CONTACT_MM2:
        return Check("bed_contact", name, Status.WARN,
                     f"only {contact:.1f} mm2 on the bed ({100 * share:.1f}% of footprint); may tip or detach",
                     measured, limit,
                     hint=(f"rotate it in Part.print so a large flat face sits on the bed (now {contact:.1f} mm2), "
                           "or add a flat base plate or a brim-sized foot to the model"))
    return Check("bed_contact", name, Status.PASS, f"{contact:.1f} mm2 on the bed ({100 * share:.0f}% of footprint)",
                 measured, limit)


def _overhang(name: str, mesh, cfg: dict) -> Check:
    result = printability.overhang(mesh, cfg["max_overhang_deg"], first_layer_mm=cfg["first_layer_mm"])
    allow = cfg["overhang_allow_mm2"]
    where = [{"centroid_mm": r["centroid_mm"], "area_mm2": r["area_mm2"], "z_mm": r["z_mm"], "frame": "print"}
             for r in result["regions"]]
    measured = {"area_mm2": result["area_mm2"], "regions": result["region_count"],
                "first_layer_area_mm2": result["first_layer_area_mm2"]}
    limit = {"max_area_mm2": allow, "max_overhang_deg": cfg["max_overhang_deg"],
             "first_layer_mm": cfg["first_layer_mm"]}
    if result["area_mm2"] <= allow:
        return Check("overhang", name, Status.PASS,
                     f"{result['area_mm2']:.1f} mm2 unsupported at {cfg['max_overhang_deg']:g} deg (allowed {allow:g})",
                     measured, limit, where)
    worst = where[0]
    return Check("overhang", name, Status.FAIL,
                 f"{result['area_mm2']:.1f} mm2 unsupported > {allow:g} allowed; worst {worst['area_mm2']:.1f} mm2 at "
                 f"z {worst['z_mm'][0]:.1f}..{worst['z_mm'][1]:.1f}", measured, limit, where,
                 hint=("rotate it in Part.print so a large flat face sits on the bed, or turn the worst face "
                       f"(where[0], {worst['area_mm2']:.0f} mm2 at z {worst['z_mm'][0]:.1f}) into a "
                       f"{cfg['max_overhang_deg']:g} degree chamfer; raise checks.overhang_allow_mm2 only if "
                       "supports are acceptable"))


def _min_wall(name: str, mesh, cfg: dict) -> Check:
    limit_mm = cfg["min_wall_mm"]
    result = wall_thickness(mesh, samples=WALL_SAMPLES, thin=limit_mm)
    if not result["hits"]:
        return Check("min_wall", name, Status.WARN, "no ray hits; mesh is open or empty, wall not measured")
    measured = {k: result[k] for k in ("min_mm", "p1_mm", "p5_mm", "median_mm", "thin_share")}
    where = ([{"centroid_mm": result["min_at_mm"], "z_mm": result["thin_z_mm"], "frame": "print"}]
             if result["thin_z_mm"] else [])
    if result["p1_mm"] >= limit_mm:
        return Check("min_wall", name, Status.PASS, f"p1 {result['p1_mm']:.2f} mm, min {result['min_mm']:.2f} mm",
                     measured, {"min_wall_mm": limit_mm})
    z = result["thin_z_mm"]
    return Check("min_wall", name, Status.FAIL,
                 f"p1 {result['p1_mm']:.2f} mm < {limit_mm:g} mm; {100 * result['thin_share']:.1f}% of surface thin"
                 + (f", z {z[0]:.1f}..{z[1]:.1f}" if z else ""), measured, {"min_wall_mm": limit_mm}, where,
                 hint=(f"thicken the thin walls to at least {limit_mm:g} mm (thinnest {result['min_mm']:.2f} mm"
                       + (f", near z {z[0]:.1f}..{z[1]:.1f}" if z else "")
                       + "); lower checks.min_wall_mm only if the nozzle can print the thinner wall"))


def _interference(a: str, b: str, shape_a: cq.Shape, shape_b: cq.Shape, tol: float, allowed: bool) -> Check:
    detail = measure.overlap(shape_a, shape_b)
    overlap = detail["volume_mm3"]
    pair = f"{a}+{b}"
    measured, limit = {"overlap_mm3": overlap}, {"max_overlap_mm3": tol}
    if overlap <= tol:
        return Check("interference", pair, Status.PASS, f"overlap {overlap:.3f} mm3", measured, limit)
    if allowed:
        return Check("interference", pair, Status.PASS, f"overlap {overlap:.1f} mm3 (listed in allow_overlap)",
                     measured, limit)
    where = [{"centroid_mm": detail["centroid_mm"], "volume_mm3": overlap, "bbox_mm": detail["bbox"], "frame": "use"}]
    return Check("interference", pair, Status.FAIL, f"overlap {overlap:.1f} mm3 > {tol:g}", measured, limit, where,
                 hint=(f"move one part with Part.use or add clearance near where[0] ({', '.join(f'{v:.1f}' for v in detail['centroid_mm'])}); "
                       "if this is a designed press fit, list the pair in checks.allow_overlap"))
