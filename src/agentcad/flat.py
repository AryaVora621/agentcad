"""2D flat parts cut on a laser, CNC router or waterjet: profile export and the flat gates.

A cut part is modelled as a 3D plate. Its print frame is the cutting frame: the sheet lies on z = 0 and the
thickness runs along +z. Every number a gate reports is measured from a section of the built solid, never read
back from a parameter.

The thin-feature gates work on the section as polygons with morphological opening and closing. The offsets run on
manifold3d's CrossSection (Clipper2 inside, already a dependency) instead of OCCT's 2D offset: Clipper keeps
tangent arcs, 0.5 mm webs and collapsing features stable, and a whole gate set on a 300 x 200 mm plate with 20 holes
takes a fraction of a second.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq
import manifold3d as mf
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.gp import gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_REVERSED

from .geometry import bbox, volume
from .report import Check, Status

FLAT_IDS: tuple[str, ...] = ("flat_profile", "sheet_fit", "min_feature", "inner_corners", "hole_size")
GATE_DOCS: dict[str, str] = {
    "flat_profile": "The part is a straight through-cut: volume equals profile area times thickness and the "
                    "sections at 10 and 90 percent of the thickness match the mid section.",
    "sheet_fit": "The outline's bounding box fits the [flat] sheet as is or turned 90 degrees.",
    "min_feature": "No web or tab narrower than min_feature_mm and no slot narrower than the kerf.",
    "inner_corners": "CNC only: inside corners and slots the endmill of tool_diameter_mm can reach.",
    "hole_size": "Circular holes are at least min_hole_ratio times the sheet thickness across.",
}

# Round joins need enough segments that arc error stays far below the area tolerances.
_SEGMENTS = 72
# Boundary sampling deflection for gates (mm); half the smallest area tolerance below is far above this noise.
_DEFLECTION = 0.005
_MAX_WHERE = 10
# A hole counts as circular above this 4*pi*A/P^2 (a regular 16-gon is 0.984, a square 0.785).
_CIRCULARITY = 0.985


@dataclass
class Loop:
    """One closed boundary: LWPOLYLINE-style vertices (x, y, bulge) plus a dense polyline for measuring."""

    vertices: list[tuple[float, float, float]]
    points: np.ndarray
    circle: tuple[float, float, float] | None
    length: float
    inner: bool


@dataclass
class Profile:
    """The straight-through section of a cut part, in sheet coordinates (outline minimum at 0, 0)."""

    thickness: float
    shift: tuple[float, float]
    loops: list[Loop]
    region: mf.CrossSection
    area: float
    outline: tuple[float, float]
    faces: list = field(default_factory=list)

    @property
    def holes(self) -> list[Loop]:
        """The inner loops."""
        return [loop for loop in self.loops if loop.inner]


def _bulge(p0: np.ndarray, pm: np.ndarray, p1: np.ndarray) -> float:
    """Signed bulge (tan of a quarter of the sweep, positive counter-clockwise) of the arc p0 - pm - p1."""
    ax, ay = p0
    bx, by = pm
    cx, cy = p1
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return 0.0
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    a0, am, a1 = (math.atan2(p[1] - uy, p[0] - ux) for p in (p0, pm, p1))

    def wrap(a: float) -> float:
        return (a + math.pi) % (2 * math.pi) - math.pi

    first, second = wrap(am - a0), wrap(a1 - am)
    if first * second < 0:
        return 0.0
    return math.tan((first + second) / 4)


def _wire_loop(wire: cq.Wire, inner: bool, deflection: float = _DEFLECTION) -> Loop:
    """Walk a wire in order and keep true lines and arcs as bulge vertices; other curves become polylines."""
    edges = wire.Edges()
    if len(edges) == 1 and edges[0].geomType() == "CIRCLE" and edges[0].IsClosed():
        # The adaptor applies the edge's placement; Edge.arcCenter would read the unmoved geometry.
        circle = BRepAdaptor_Curve(edges[0].wrapped).Circle()
        centre, radius = circle.Location(), circle.Radius()
        count = max(16, math.ceil(math.pi / math.acos(min(1.0, max(-1.0, 1 - deflection / radius)))))
        angles = np.linspace(0, 2 * math.pi, count, endpoint=False)
        points = np.column_stack([centre.X() + radius * np.cos(angles), centre.Y() + radius * np.sin(angles)])
        return Loop([], points, (centre.X(), centre.Y(), radius), 2 * math.pi * radius, inner)
    vertices: list[tuple[float, float, float]] = []
    dense: list[tuple[float, float]] = []
    explorer = BRepTools_WireExplorer(wire.wrapped)
    while explorer.More():
        edge = explorer.Current()
        curve = BRepAdaptor_Curve(edge)
        kind = cq.Edge(edge).geomType()
        if kind == "LINE":
            sampled = [curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())]
        else:
            sampler = GCPnts_QuasiUniformDeflection(curve, deflection)
            sampled = [sampler.Value(i) for i in range(1, sampler.NbPoints() + 1)]
        points = [(p.X(), p.Y()) for p in sampled]
        if edge.Orientation() == TopAbs_REVERSED:
            points.reverse()
        bulge = 0.0
        if kind == "CIRCLE":
            middle = points[len(points) // 2]
            bulge = _bulge(np.array(points[0]), np.array(middle), np.array(points[-1]))
        if kind in ("LINE", "CIRCLE"):
            vertices.append((points[0][0], points[0][1], bulge))
        else:
            vertices += [(x, y, 0.0) for x, y in points[:-1]]
        dense += points[:-1]
        explorer.Next()
    array = np.array(dense)
    return Loop(vertices, array, None, float(wire.Length()), inner)


def _section(solid: cq.Shape, z: float) -> list[cq.Face]:
    size = max(bbox(solid)["size_mm"]) * 4 + 100
    centre = [(a + b) / 2 for a, b in zip(bbox(solid)["min_mm"], bbox(solid)["max_mm"], strict=True)]
    cutter = cq.Face.makePlane(size, size, basePnt=(centre[0], centre[1], z))
    return [f for f in solid.intersect(cutter).Faces() if f.Area() > 1e-9]


def build_profile(solid: cq.Shape, at: float = 0.5, shift: tuple[float, float] | None = None) -> Profile | None:
    """Section a print-frame solid at ``at`` of its thickness; None when the plane misses the solid.

    ``shift`` pins the sheet origin so sections at several heights stay comparable; by default the outline
    minimum becomes (0, 0).
    """
    box = bbox(solid)
    thickness = box["size_mm"][2]
    faces = _section(solid, box["min_mm"][2] + at * thickness)
    if not faces:
        return None
    if shift is None:
        shift = (-min(f.BoundingBox().xmin for f in faces), -min(f.BoundingBox().ymin for f in faces))
    # transformShape bakes the move into the geometry; a mere placement makes offset2D apply it twice.
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(shift[0], shift[1], -(box["min_mm"][2] + at * thickness)))
    moved = [cq.Face(BRepBuilderAPI_Transform(f.wrapped, move, True).Shape()) for f in faces]
    loops = [_wire_loop(w, inner) for f in moved for w, inner in
             [(f.outerWire(), False), *[(iw, True) for iw in f.innerWires()]]]
    region = mf.CrossSection([loop.points for loop in loops], mf.FillRule.EvenOdd)
    low_x, low_y, high_x, high_y = region.bounds()
    return Profile(thickness, shift, loops, region, sum(f.Area() for f in moved),
                   (high_x - low_x, high_y - low_y), moved)


def _offset(region: mf.CrossSection, delta: float) -> mf.CrossSection:
    return region.offset(delta, mf.JoinType.Round, 2.0, _SEGMENTS)


def _opening_loss(region: mf.CrossSection, radius: float) -> mf.CrossSection:
    """Material an opening by ``radius`` removes: webs and tabs narrower than 2 radius, plus convex corner crumbs."""
    return region - _offset(_offset(region, -radius), radius)


def _closing_gain(region: mf.CrossSection, radius: float) -> mf.CrossSection:
    """Area a closing by ``radius`` adds: slots narrower than 2 radius, plus concave corner crumbs."""
    return _offset(_offset(region, radius), -radius) - region


def _regions(pieces: mf.CrossSection, min_area: float) -> list[dict]:
    found = []
    for piece in pieces.decompose():
        area = piece.area()
        if area >= min_area:
            x0, y0, x1, y1 = piece.bounds()
            found.append({"centre_mm": [(x0 + x1) / 2, (y0 + y1) / 2], "size_mm": [x1 - x0, y1 - y0],
                          "area_mm2": area})
    return sorted(found, key=lambda r: -r["area_mm2"])


def _crumb_tolerance(radius: float, floor: float) -> float:
    # A sharp 90 degree corner loses (1 - pi/4) r^2 = 0.21 r^2 to a rounded opening or closing, and corners down to
    # about 75 degrees stay under 0.4 r^2. A 0.2 x 5 mm sliver is 1 mm2, well above 0.1 mm2 at the default radius.
    return max(floor, 0.4 * radius * radius)


def _where(found: list[dict], **extra: object) -> list[dict]:
    return [{**extra, **r} for r in found[:_MAX_WHERE]]


def _profile_check(part_name: str, solid: cq.Shape, mid: Profile) -> Check:
    vol = volume(solid)
    expected = mid.area * mid.thickness
    error = abs(vol - expected) / vol if vol else 1.0
    tolerance = 0.05 + 1e-4 * mid.area
    mismatches, where = {}, []
    for at in (0.1, 0.9):
        other = build_profile(solid, at, mid.shift)
        diff = (mid.region - other.region) + (other.region - mid.region) if other else mid.region
        found = _regions(diff, tolerance)
        mismatches[at] = diff.area()
        where += _where(found, z_mm=round(at * mid.thickness, 4))
    measured = {"volume_mm3": vol, "profile_area_mm2": mid.area, "thickness_mm": mid.thickness,
                "volume_error": error, "mismatch_10pct_mm2": mismatches[0.1], "mismatch_90pct_mm2": mismatches[0.9]}
    limit = {"max_volume_error": 0.01, "max_section_mismatch_mm2": tolerance}
    if error <= 0.01 and not where:
        return Check("flat_profile", part_name, Status.PASS,
                     f"straight through-cut, {mid.area:.1f} mm2 x {mid.thickness:g} mm", measured, limit)
    reason = f"volume is {100 * error:.1f}% off profile area x thickness" if error > 0.01 else \
        "sections at 10% and 90% of the thickness differ from the mid section"
    return Check("flat_profile", part_name, Status.FAIL, f"not a straight through-cut: {reason}", measured, limit,
                 where[:_MAX_WHERE],
                 hint="Cutters only go straight through: move pockets, steps or engraving to another part or a "
                      "3D print.")


def _sheet_check(part_name: str, profile: Profile, config: dict) -> Check:
    width, height = profile.outline
    sheet_w, sheet_h = config["sheet"]
    plain = width <= sheet_w and height <= sheet_h
    turned = height <= sheet_w and width <= sheet_h
    measured = {"outline_mm": [width, height], "rotated": bool(turned and not plain)}
    limit = {"sheet_mm": [sheet_w, sheet_h]}
    sheet = f"{sheet_w:g} x {sheet_h:g}"
    if plain or turned:
        note = "" if plain else " (turn 90 degrees)"
        return Check("sheet_fit", part_name, Status.PASS, f"{width:.1f} x {height:.1f} mm fits {sheet} mm{note}",
                     measured, limit)
    return Check("sheet_fit", part_name, Status.FAIL, f"{width:.1f} x {height:.1f} mm does not fit {sheet} mm",
                 measured, limit, hint=f"Split the part or set [flat].sheet to a larger sheet than {sheet} mm.")


def _min_feature_check(part_name: str, profile: Profile, config: dict) -> Check:
    web, kerf = config["min_feature_mm"], config["kerf_mm"]
    if web <= 0 and kerf <= 0:
        return Check("min_feature", part_name, Status.SKIP, "disabled; set [flat].min_feature_mm or kerf_mm")
    webs = _regions(_opening_loss(profile.region, web / 2), _crumb_tolerance(web / 2, 0.01)) if web > 0 else []
    slots = _regions(_closing_gain(profile.region, kerf / 2), _crumb_tolerance(kerf / 2, 0.005)) if kerf > 0 else []
    measured = {"narrow_webs": len(webs), "narrow_slots": len(slots), "web_area_mm2": sum(r["area_mm2"] for r in webs),
                "slot_area_mm2": sum(r["area_mm2"] for r in slots)}
    limit = {"min_feature_mm": web, "kerf_mm": kerf}
    if not webs and not slots:
        return Check("min_feature", part_name, Status.PASS,
                     f"no web under {web:g} mm and no slot under the {kerf:g} mm kerf", measured, limit)
    where = sorted([*_where(webs, kind="web"), *_where(slots, kind="slot")], key=lambda r: -r["area_mm2"])
    first = where[0]
    return Check("min_feature", part_name, Status.FAIL,
                 f"{len(webs)} web(s) under {web:g} mm and {len(slots)} slot(s) under the {kerf:g} mm kerf; "
                 f"worst {first['kind']} near ({first['centre_mm'][0]:.1f}, {first['centre_mm'][1]:.1f})",
                 measured, limit, where[:_MAX_WHERE],
                 hint=f"Widen webs and tabs to at least {web:g} mm and slots to more than {kerf:g} mm, or drop "
                      "them; they burn out, break or close up when cut.")


def _inner_corner_check(part_name: str, profile: Profile, config: dict) -> Check:
    tool = config["tool_diameter_mm"]
    if tool <= 0:
        return Check("inner_corners", part_name, Status.SKIP, "disabled; set [flat].tool_diameter_mm")
    found = _regions(_closing_gain(profile.region, tool / 2), 0.02)
    measured = {"unreachable_regions": len(found), "unreachable_area_mm2": sum(r["area_mm2"] for r in found)}
    limit = {"tool_diameter_mm": tool, "min_corner_radius_mm": tool / 2}
    if not found:
        return Check("inner_corners", part_name, Status.PASS,
                     f"every inside corner and slot is reachable by a {tool:g} mm endmill", measured, limit)
    first = found[0]
    return Check("inner_corners", part_name, Status.WARN,
                 f"{len(found)} spot(s) a {tool:g} mm endmill cannot reach; worst {first['area_mm2']:.2f} mm2 near "
                 f"({first['centre_mm'][0]:.1f}, {first['centre_mm'][1]:.1f})", measured, limit,
                 _where(found),
                 hint=f"Add dogbones or give inside corners a radius of at least {tool / 2:.2f} mm, or widen "
                      f"slots beyond {tool:g} mm.")


def _hole_size_check(part_name: str, profile: Profile, config: dict) -> Check:
    ratio = config["min_hole_ratio"]
    if ratio <= 0:
        return Check("hole_size", part_name, Status.SKIP, "disabled; set [flat].min_hole_ratio")
    holes = []
    for loop in profile.holes:
        if loop.circle:
            x, y, radius = loop.circle
            holes.append((2 * radius, x, y))
            continue
        pts = loop.points
        area = abs(0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1]))
        perimeter = np.sum(np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1))
        if perimeter > 0 and 4 * math.pi * area / perimeter**2 >= _CIRCULARITY:
            holes.append((2 * math.sqrt(area / math.pi), *pts.mean(axis=0)))
    minimum = ratio * profile.thickness
    small = sorted((h for h in holes if h[0] < minimum), key=lambda h: h[0])
    measured = {"circular_holes": len(holes), "smallest_diameter_mm": min((h[0] for h in holes), default=None),
                "thickness_mm": profile.thickness}
    limit = {"min_diameter_mm": minimum, "min_hole_ratio": ratio}
    if not small:
        text = f"{len(holes)} circular hole(s), all at least {minimum:g} mm" if holes else "no circular holes"
        return Check("hole_size", part_name, Status.PASS, text, measured, limit)
    where = [{"centre_mm": [x, y], "diameter_mm": d} for d, x, y in small[:_MAX_WHERE]]
    return Check("hole_size", part_name, Status.WARN,
                 f"{len(small)} hole(s) under {minimum:g} mm ({ratio:g} x the {profile.thickness:g} mm sheet); "
                 f"smallest {small[0][0]:.2f} mm", measured, limit, where,
                 hint=f"Holes under {minimum:g} mm across char or cut unevenly in {profile.thickness:g} mm stock; "
                      "enlarge them or lower [flat].min_hole_ratio.")


def flat_gates(ctx, wanted: set[str] | None) -> list[Check]:
    """Return the flat gates' checks for every cut part, gate by gate in ``FLAT_IDS`` order."""
    cut_parts = [p for p in ctx.project.parts.values() if getattr(p, "cut", None) is not None]
    if not cut_parts:
        return []
    config = ctx.project.flat
    profiles: dict[str, Profile | None] = {}
    out: list[Check] = []

    def profile_of(part) -> Profile | None:
        if part.name not in profiles:
            profiles[part.name] = build_profile(ctx.solid(part.name, "print"))
        return profiles[part.name]

    def run(gate_id: str, part, gate) -> None:
        try:
            profile = profile_of(part)
            if profile is None:
                out.append(Check(gate_id, part.name, Status.FAIL if gate_id == "flat_profile" else Status.SKIP,
                                 "the mid-thickness plane misses the part; no profile to measure",
                                 hint="Model the cut part as a solid plate lying on z = 0 in its print frame."
                                 if gate_id == "flat_profile" else None))
                return
            out.append(gate(part.name, profile))
        except Exception as exc:  # noqa: BLE001  a broken gate must not hide the other results
            out.append(Check(gate_id, part.name, Status.ERROR, f"{type(exc).__name__}: {exc}"))

    for gate_id in FLAT_IDS:
        if wanted is not None and gate_id not in wanted:
            continue
        for part in cut_parts:
            if gate_id == "inner_corners" and part.cut != "cnc":
                continue
            gates = {"flat_profile": lambda name, profile, part=part: _profile_check(
                         name, ctx.solid(part.name, "print"), profile),
                     "sheet_fit": lambda name, profile: _sheet_check(name, profile, config),
                     "min_feature": lambda name, profile: _min_feature_check(name, profile, config),
                     "inner_corners": lambda name, profile: _inner_corner_check(name, profile, config),
                     "hole_size": lambda name, profile: _hole_size_check(name, profile, config)}
            run(gate_id, part, gates[gate_id])
    return out


def compensated_loops(profile: Profile, kerf: float) -> list[Loop]:
    """Loops of the profile grown by kerf/2 (outline outward, holes inward), so the cut leaves nominal size."""
    if kerf <= 0:
        return profile.loops
    loops: list[Loop] = []
    radius = kerf / 2
    for face in profile.faces:
        for wire, inner in [(face.outerWire(), False), *[(w, True) for w in face.innerWires()]]:
            loop = _wire_loop(wire, inner)
            alone = mf.CrossSection([loop.points], mf.FillRule.EvenOdd)
            # OCCT's 2D offset segfaults on a feature that collapses, so prove the offset is safe on the polygon first:
            # a hole must survive shrinking by the kerf, an outline must not have notches the growth would close.
            lost = _opening_loss(alone, radius).area() if inner else _closing_gain(alone, radius).area()
            # A hole smaller than the kerf loses less than the crumb tolerance in area, so test for vanishing too.
            vanishes = inner and _offset(alone, -radius).is_empty()
            if vanishes or lost > _crumb_tolerance(radius, 0.005):
                where = wire.Center()
                raise ValueError(f"kerf compensation of {kerf:g} mm collapses a {'hole' if inner else 'notch'} "
                                 f"near ({where.x:.1f}, {where.y:.1f}); enlarge it or turn compensate_kerf off")
            grown = wire.offset2D(-radius if inner else radius, "arc")
            if len(grown) != 1:
                raise ValueError(f"kerf compensation of {kerf:g} mm failed near ({wire.Center().x:.1f}, "
                                 f"{wire.Center().y:.1f}); turn compensate_kerf off")
            loops.append(_wire_loop(grown[0], inner))
    return loops


def write_dxf(loops: list[Loop], path: Path) -> None:
    """Write closed loops as CIRCLE and closed LWPOLYLINE entities in millimetres (R2010)."""
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.layers.add("CUT", color=1)
    space = doc.modelspace()
    for loop in loops:
        if loop.circle:
            space.add_circle(loop.circle[:2], loop.circle[2], dxfattribs={"layer": "CUT"})
        else:
            space.add_lwpolyline(loop.vertices, format="xyb", close=True, dxfattribs={"layer": "CUT"})
    doc.saveas(str(path))


def write_svg(loops: list[Loop], width: float, height: float, path: Path) -> None:
    """Write the loops as an SVG in millimetres with y pointing down, so it looks like the DXF."""
    def flip(y: float) -> float:
        return height - y

    paths = []
    for loop in loops:
        if loop.circle:
            x, y, r = loop.circle
            paths.append(f'<circle cx="{x:.4f}" cy="{flip(y):.4f}" r="{r:.4f}"/>')
            continue
        verts = loop.vertices
        commands = [f"M {verts[0][0]:.4f} {flip(verts[0][1]):.4f}"]
        for index, (x, y, bulge) in enumerate(verts):
            nx, ny, _ = verts[(index + 1) % len(verts)]
            if abs(bulge) < 1e-9:
                commands.append(f"L {nx:.4f} {flip(ny):.4f}")
                continue
            chord = math.hypot(nx - x, ny - y)
            radius = chord * (1 + bulge * bulge) / (4 * abs(bulge))
            commands.append(f"A {radius:.4f} {radius:.4f} 0 {int(abs(bulge) > 1)} {int(bulge < 0)} "
                            f"{nx:.4f} {flip(ny):.4f}")
        paths.append(f'<path d="{" ".join(commands)} Z"/>')
    body = "\n  ".join(paths)
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.4f}mm" height="{height:.4f}mm" '
                    f'viewBox="0 0 {width:.4f} {height:.4f}">\n'
                    f'  <g fill="none" stroke="#000000" stroke-width="0.1">\n  {body}\n  </g>\n</svg>\n')


def export_profile(solid: cq.Shape, config: dict, dxf_path: Path, svg_path: Path) -> dict:
    """Write the DXF and SVG of a print-frame solid's profile and return its manifest ``flat`` record (no hashes)."""
    profile = build_profile(solid)
    if profile is None:
        raise ValueError("the mid-thickness plane misses the part; model it as a solid plate lying on z = 0")
    kerf = config["kerf_mm"] if config["compensate_kerf"] else 0.0
    loops = compensated_loops(profile, kerf)
    # Compensation grows the outline by kerf/2 per side, so re-anchor the minimum at the sheet origin.
    low = np.min(np.vstack([loop.points for loop in loops if not loop.inner]), axis=0)
    if kerf > 0:
        loops = [_moved(loop, -low[0], -low[1]) for loop in loops]
    high = np.max(np.vstack([loop.points for loop in loops if not loop.inner]), axis=0)
    write_dxf(loops, dxf_path)
    write_svg(loops, float(high[0]), float(high[1]), svg_path)
    return {"thickness_mm": profile.thickness, "profile_area_mm2": profile.area,
            "cut_length_mm": sum(loop.length for loop in loops), "holes": len(profile.holes),
            "outline_mm": list(profile.outline), "kerf_compensated": kerf > 0,
            "kerf_mm": kerf if kerf > 0 else None}


def _moved(loop: Loop, dx: float, dy: float) -> Loop:
    circle = (loop.circle[0] + dx, loop.circle[1] + dy, loop.circle[2]) if loop.circle else None
    return Loop([(x + dx, y + dy, b) for x, y, b in loop.vertices], loop.points + np.array([dx, dy]), circle,
                loop.length, loop.inner)
