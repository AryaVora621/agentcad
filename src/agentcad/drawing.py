"""Dimensioned 2D drawings (SVG, PDF, PNG) taken from the measured geometry.

Every number on a sheet is read from the built solid (its exact bounding box and its circular edges), never from a
parameter of the model code. Lines come from OCCT hidden-line removal; layout, scale and dimension placement are
computed here so that nothing overlaps (``Sheet.conflicts`` checks that and the tests run it).

Coordinates: sheet millimetres with the origin at the lower-left corner and y up while a sheet is built; the JSON
document reports view boxes from the upper-left corner with y down, like the SVG.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cadquery as cq

from . import __version__
from .geometry import bbox
from .project import BuildError, ConfigError, Project

SCHEMA = "agentcad.drawing/1"
FORMATS = ("svg", "pdf", "png")
DPI = 150
MESH_SUFFIXES = {".stl", ".obj", ".ply", ".3mf", ".glb"}

# Sheets, landscape, millimetres.
SHEETS: dict[str, tuple[float, float]] = {"A4": (297.0, 210.0), "A3": (420.0, 297.0),
                                          "letter": (279.4, 215.9), "tabloid": (431.8, 279.4)}
SCALES: tuple[tuple[float, str], ...] = ((10, "10:1"), (5, "5:1"), (2, "2:1"), (1, "1:1"), (0.5, "1:2"),
                                         (0.2, "1:5"), (0.1, "1:10"), (0.05, "1:20"), (0.02, "1:50"))

# Drawing standards, all in sheet millimetres.
FRAME = 7.0            # sheet edge to frame line
AREA_PAD = 5.0         # frame line to usable area
TITLE_W, TITLE_H = 130.0, 33.0
TEXT = 3.5             # dimension text size (font em height)
SMALL = 2.4            # labels, footer
DIM_OFF = 9.0          # first dimension line off the view (ISO 129: 8 to 10)
EXT_GAP, EXT_OVER = 1.0, 2.0
ARROW_L, ARROW_W = 3.0, 1.0
ORD_OFF = 6.0          # ordinate rows and columns off the view
PAD = 0.4              # clear space around text boxes
CLEAR = 1.2            # extra air a callout keeps from other text and dimension lines
CELL_GAP = 5.0
HOLE_TABLE_MIN = 9     # more hole centres than 8 in a view switch to a table
ROW_H = 4.6            # hole table row height
LW = {"visible": 0.5, "tangent": 0.25, "hidden": 0.25, "dim": 0.18, "ext": 0.18, "leader": 0.18, "centre": 0.13,
      "frame": 0.7, "title": 0.35}
MM_PT = 72.0 / 25.4

# Orthographic views: (name, normal toward the viewer, horizontal axis); the vertical axis is normal x horizontal.
_ISO_N = (1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3))
_ISO_X = (1 / math.sqrt(2), 1 / math.sqrt(2), 0.0)
VIEWS = {"front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)), "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
         "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), "isometric": (_ISO_N, _ISO_X)}
LABELS = {"front": "FRONT", "top": "TOP", "right": "RIGHT", "isometric": "ISOMETRIC"}


# ---------------------------------------------------------------------------------------------------------------
# small helpers

def fmt(value: float) -> str:
    """Up to two decimals, trailing zeros dropped (``3.40`` -> ``3.4``, ``60.0`` -> ``60``)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


@lru_cache(maxsize=4096)
def text_width(text: str, size: float) -> float:
    """Width of a string in the sheet font, measured from glyph outlines so layout needs no renderer."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    if not text:
        return 0.0
    width = TextPath((0, 0), text, size=size, prop=FontProperties(family="DejaVu Sans")).get_extents().width
    return float(width + 0.12 * size)


Box = tuple[float, float, float, float]


def _overlap(a: Box, b: Box, tol: float = 1e-6) -> bool:
    return a[0] < b[2] - tol and b[0] < a[2] - tol and a[1] < b[3] - tol and b[1] < a[3] - tol


def _seg_hits(seg: tuple[float, float, float, float], box: Box) -> bool:
    """Liang-Barsky clip of a segment against a box."""
    x0, y0, x1, y1 = seg
    dx, dy, t0, t1 = x1 - x0, y1 - y0, 0.0, 1.0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _union(a: Box | None, b: Box) -> Box:
    return b if a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


# ---------------------------------------------------------------------------------------------------------------
# primitives

@dataclass
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str


@dataclass
class Txt:
    x: float
    y: float            # baseline anchor
    text: str
    size: float = TEXT
    rot: int = 0
    ha: str = "center"
    inside: bool = False   # id labels next to holes may sit on the view

    @property
    def box(self) -> Box:
        w, up = text_width(self.text, self.size) + 2 * PAD, 0.76 * self.size + PAD
        if self.rot == 90:
            return (self.x - up, self.y - w / 2, self.x + PAD, self.y + w / 2)
        left = self.x - w / 2 if self.ha == "center" else self.x - PAD
        return (left, self.y - PAD, left + w, self.y + up)


@dataclass
class DimRecord:
    view: str
    kind: str
    role: str
    value: float
    count: int
    text: str


@dataclass
class Hole:
    x: float            # view datum relative, model mm
    y: float
    d: float


class Cell:
    """One view with its dimensions, in coordinates whose origin is the view's lower-left corner."""

    def __init__(self, name: str, vw: float, vh: float) -> None:
        self.name, self.vw, self.vh = name, vw, vh
        self.segs: list[Seg] = []
        self.arrows: list[list[tuple[float, float]]] = []
        self.texts: list[Txt] = []
        self.records: list[DimRecord] = []
        self.table: list[tuple[str, float, float, float]] = []

    @property
    def view_box(self) -> Box:
        return (0.0, 0.0, self.vw, self.vh)

    def envelope(self) -> Box:
        env = self.view_box
        for s in self.segs:
            env = _union(env, (min(s.x0, s.x1), min(s.y0, s.y1), max(s.x0, s.x1), max(s.y0, s.y1)))
        for pts in self.arrows:
            env = _union(env, (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts),
                               max(p[1] for p in pts)))
        for t in self.texts:
            env = _union(env, t.box)
        return env

    # -- dimension building blocks -------------------------------------------------------------------------
    def arrow(self, x: float, y: float, dx: float, dy: float) -> None:
        """Closed filled arrowhead with its tip at (x, y) pointing along (dx, dy)."""
        norm = math.hypot(dx, dy) or 1.0
        ux, uy = dx / norm, dy / norm
        bx, by = x - ux * ARROW_L, y - uy * ARROW_L
        self.arrows.append([(x, y), (bx - uy * ARROW_W / 2, by + ux * ARROW_W / 2),
                            (bx + uy * ARROW_W / 2, by - ux * ARROW_W / 2)])

    def linear(self, horizontal: bool, a: float, b: float, obj: float, line: float, text: str) -> None:
        """A dimension between a and b along one axis, measured off an object edge at ``obj``, drawn at ``line``."""
        sign = 1.0 if line > obj else -1.0
        span, w = abs(b - a), text_width(text, TEXT) + 2 * PAD
        outside = span < 2 * ARROW_L + 1
        if horizontal:
            for p in (a, b):
                self.segs.append(Seg(p, obj + sign * EXT_GAP, p, line + sign * EXT_OVER, "ext"))
        else:
            for p in (a, b):
                self.segs.append(Seg(obj + sign * EXT_GAP, p, line + sign * EXT_OVER, p, "ext"))
        lo, hi = min(a, b), max(a, b)
        pt = (lambda t: (t, line)) if horizontal else (lambda t: (line, t))
        along = (1, 0) if horizontal else (0, 1)
        if outside:
            # Too short for arrows between the extension lines: arrows sit outside, pointing in.
            self.segs.append(Seg(*pt(lo - ARROW_L), *pt(hi + ARROW_L), "dim"))
            self.arrow(*pt(lo), *along)
            self.arrow(*pt(hi), -along[0], -along[1])
        else:
            self.segs.append(Seg(*pt(lo), *pt(hi), "dim"))
            self.arrow(*pt(lo), -along[0], -along[1])
            self.arrow(*pt(hi), *along)
        mid = (a + b) / 2
        fits = w <= span - 1.0
        glyph = 0.76 * TEXT
        # Text sits beside the line when it fits between the extension lines, else beyond their tips.
        if horizontal:
            base = line + 1.0 if fits else (line + EXT_OVER + 0.8 if sign > 0 else line - EXT_OVER - 0.8 - glyph)
            self.texts.append(Txt(mid, base, text))
        else:
            gap = 1.0 if fits else EXT_OVER + 0.8
            x = line - gap if sign < 0 else line + gap + glyph
            self.texts.append(Txt(x, mid, text, rot=90))


# ---------------------------------------------------------------------------------------------------------------
# geometry: projected views, holes, digest

@dataclass
class ProjectedView:
    name: str
    normal: tuple[float, float, float]
    xdir: tuple[float, float, float]
    visible: list[cq.Edge] = field(default_factory=list)
    tangent: list[cq.Edge] = field(default_factory=list)
    hidden: list[cq.Edge] = field(default_factory=list)
    bounds: Box = (0.0, 0.0, 0.0, 0.0)     # x0, y0, x1, y1 in view coordinates (model mm)
    holes: list[Hole] = field(default_factory=list)

    @property
    def ydir(self) -> tuple[float, float, float]:
        n, x = self.normal, self.xdir
        return (n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0])

    @property
    def size(self) -> tuple[float, float]:
        return self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1]

    def project(self, p: tuple[float, float, float]) -> tuple[float, float]:
        y = self.ydir
        return (sum(p[i] * self.xdir[i] for i in range(3)), sum(p[i] * y[i] for i in range(3)))

    @property
    def sight(self) -> list[float]:
        return [round(-c, 6) + 0.0 for c in self.normal]


def _edges_of(compound) -> list[cq.Edge]:
    if compound is None or compound.IsNull():
        return []
    return list(cq.Compound(compound).Edges())


def _sample(edge: cq.Edge, deflection: float) -> list[tuple[float, float]]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    curve = BRepAdaptor_Curve(edge.wrapped)
    pts = GCPnts_QuasiUniformDeflection(curve, max(deflection, 1e-4))
    if pts.IsDone() and pts.NbPoints() >= 2:
        return [(pts.Value(i).X(), pts.Value(i).Y()) for i in range(1, pts.NbPoints() + 1)]
    a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
    return [(a.X(), a.Y()), (b.X(), b.Y())]


def _hlr(shape: cq.Shape, view: ProjectedView) -> None:
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape

    algo = HLRBRep_Algo()
    algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*view.normal), gp_Dir(*view.xdir))))
    algo.Update()
    algo.Hide()
    result = HLRBRep_HLRToShape(algo)
    view.visible = _edges_of(result.VCompound()) + _edges_of(result.OutLineVCompound())
    view.tangent = _edges_of(result.Rg1LineVCompound())
    view.hidden = _edges_of(result.HCompound()) + _edges_of(result.OutLineHCompound())


def _full_circles(shape: cq.Shape) -> list[tuple[tuple[float, float, float], tuple[float, float, float], float]]:
    """Circles that close (one edge or several arcs on the same circle): centre, axis, radius."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle

    spans: dict[tuple, float] = {}
    for edge in shape.Edges():
        curve = BRepAdaptor_Curve(edge.wrapped)
        if curve.GetType() != GeomAbs_Circle:
            continue
        circle = curve.Circle()
        c, a = circle.Location(), circle.Axis().Direction()
        axis = (a.X(), a.Y(), a.Z())
        if next(v for v in axis if abs(v) > 1e-9) < 0:
            axis = (-axis[0], -axis[1], -axis[2])
        key = (round(c.X(), 3), round(c.Y(), 3), round(c.Z(), 3), round(axis[0], 4), round(axis[1], 4),
               round(axis[2], 4), round(circle.Radius(), 4))
        spans[key] = spans.get(key, 0.0) + abs(curve.LastParameter() - curve.FirstParameter())
    return [((k[0], k[1], k[2]), (k[3], k[4], k[5]), k[6]) for k, span in spans.items() if span >= 2 * math.pi - 1e-3]


def project_views(shape: cq.Shape) -> dict[str, ProjectedView]:
    """Hidden-line projections of the four views plus the holes seen end-on, all from the solid itself."""
    box = bbox(shape)
    low, high = box["min_mm"], box["max_mm"]
    circles = _full_circles(shape)
    views: dict[str, ProjectedView] = {}
    for name, (normal, xdir) in VIEWS.items():
        view = ProjectedView(name, normal, xdir)
        _hlr(shape, view)
        if name == "isometric":
            xs, ys = [], []
            for edge in view.visible + view.tangent + view.hidden:
                for x, y in _sample(edge, 0.05):
                    xs.append(x)
                    ys.append(y)
            view.bounds = (min(xs), min(ys), max(xs), max(ys))
        else:
            # Exact: an axis-aligned view of the solid spans the 3D bounding box on its two axes.
            corners = [view.project((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1])
                       for z in (low[2], high[2])]
            view.bounds = (min(c[0] for c in corners), min(c[1] for c in corners),
                           max(c[0] for c in corners), max(c[1] for c in corners))
            seen = set()
            for centre, axis, radius in circles:
                if abs(sum(axis[i] * normal[i] for i in range(3))) < 0.9999 or radius < 0.05:
                    continue
                x, y = view.project(centre)
                key = (round(x, 3), round(y, 3), round(radius, 3))
                if key not in seen:
                    seen.add(key)
                    view.holes.append(Hole(x - view.bounds[0], y - view.bounds[1], 2 * radius))
        views[name] = view
    return views


def geometry_digest(shape: cq.Shape) -> str:
    """First 12 hex digits of the sha256 of the part's STEP data (header and writer counter left out)."""
    with tempfile.TemporaryDirectory(prefix="agentcad-draw-") as folder:
        path = Path(folder) / "part.step"
        cq.exporters.export(shape, str(path))
        data = path.read_bytes()
    body = data[data.index(b"DATA;"):] if b"DATA;" in data else data
    body = re.sub(rb"Open CASCADE STEP translator [\d.]+ \d+", b"Open CASCADE STEP translator", body)
    return hashlib.sha256(body).hexdigest()[:12]


# ---------------------------------------------------------------------------------------------------------------
# cell building: dimensions around one view

def _spread(wanted: list[float], widths: list[float], gap: float) -> list[float]:
    """Push sorted label centres apart so neighbours keep ``gap`` between their extents."""
    placed: list[float] = []
    for i, pos in enumerate(wanted):
        if placed:
            pos = max(pos, placed[-1] + (widths[i - 1] + widths[i]) / 2 + gap)
        placed.append(pos)
    return placed


def _group_holes(holes: list[Hole]) -> list[tuple[float, list[Hole]]]:
    groups: dict[float, list[Hole]] = {}
    for hole in holes:
        groups.setdefault(round(hole.d, 2), []).append(hole)
    return [(d, groups[d]) for d in sorted(groups, reverse=True)]


def _centres(holes: list[Hole]) -> list[Hole]:
    seen: dict[tuple[float, float], Hole] = {}
    for hole in holes:
        seen.setdefault((round(hole.x, 2), round(hole.y, 2)), hole)
    return list(seen.values())


def build_cell(view: ProjectedView, s: float, overall: tuple[str, ...], table_ids: dict | None) -> Cell:
    """Place overall dimensions, hole positions and diameter callouts around a view drawn at scale ``s``."""
    w, h = view.size
    cell = Cell(view.name, w * s, h * s)
    vw, vh = cell.vw, cell.vh
    if "width" in overall and w > 1e-6:
        cell.linear(True, 0, vw, 0, -DIM_OFF, fmt(w))
        cell.records.append(DimRecord(view.name, "linear", "width", w, 1, fmt(w)))
    if "height" in overall and h > 1e-6:
        cell.linear(False, 0, vh, 0, -DIM_OFF, fmt(h))
        cell.records.append(DimRecord(view.name, "linear", "height", h, 1, fmt(h)))
    if "depth" in overall and h > 1e-6:
        cell.linear(False, 0, vh, 0, -DIM_OFF, fmt(h))
        cell.records.append(DimRecord(view.name, "linear", "depth", h, 1, fmt(h)))
    centres = _centres(view.holes)
    for c in centres:
        r = c.d * s / 2
        for k in range(2):
            a, b = (c.x * s - r - 1.5, c.y * s), (c.x * s + r + 1.5, c.y * s)
            if k:
                a, b = (c.x * s, c.y * s - r - 1.5), (c.x * s, c.y * s + r + 1.5)
            cell.segs.append(Seg(a[0], a[1], b[0], b[1], "centre"))
    if centres and len(centres) < HOLE_TABLE_MIN:
        _ordinates(cell, view.name, centres, s)
    elif centres and table_ids is not None:
        for c in centres:
            ident = table_ids[(round(c.x, 2), round(c.y, 2))]
            cell.table.append((ident, c.x, c.y, c.d))
            _id_label(cell, c, s, ident)
    for d, group in _group_holes(view.holes):
        members = _centres(group)
        count = len(members)
        text = f"{count}x Ø{fmt(d)}" if count > 1 else f"Ø{fmt(d)}"
        target = max(members, key=lambda m: (round(m.y, 2), -m.x))
        _callout(cell, target, s, text)
        cell.records.append(DimRecord(view.name, "diameter", "diameter",
                                      sum(m.d for m in group) / len(group), count, text))
    label = LABELS[view.name]
    env = cell.envelope()
    cell.texts.append(Txt(vw / 2, env[1] - 1.2 - 0.76 * SMALL, label, SMALL))
    return cell


def _ordinates(cell: Cell, view: str, centres: list[Hole], s: float) -> None:
    """Hole centre positions from the lower-left datum: x values above the view, y values to its right."""
    vw, vh = cell.vw, cell.vh
    xs = sorted({round(c.x, 2) for c in centres})
    ys = sorted({round(c.y, 2) for c in centres})
    top = {x: max((c for c in centres if round(c.x, 2) == x), key=lambda m: m.y) for x in xs}
    right = {y: max((c for c in centres if round(c.y, 2) == y), key=lambda m: m.x) for y in ys}
    row = vh + ORD_OFF
    wanted = [0.0] + [x * s for x in xs]
    labels = ["0"] + [fmt(x) for x in xs]
    starts = [vh + EXT_GAP] + [top[x].y * s + top[x].d * s / 2 + 1.0 for x in xs]
    placed = _spread(wanted, [text_width(t, TEXT) + 2 * PAD for t in labels], 0.8)
    for want, pos, label, start in zip(wanted, placed, labels, starts):
        if abs(pos - want) < 1e-6:
            cell.segs.append(Seg(want, start, want, row, "ext"))
            cell.texts.append(Txt(want, row + 0.8, label))
        else:
            cell.segs.append(Seg(want, start, want, row, "ext"))
            cell.segs.append(Seg(want, row, pos, row + 1.6, "ext"))
            cell.texts.append(Txt(pos, row + 2.4, label))
    col = vw + ORD_OFF
    wanted = [0.0] + [y * s for y in ys]
    labels = ["0"] + [fmt(y) for y in ys]
    starts = [vw + EXT_GAP] + [right[y].x * s + right[y].d * s / 2 + 1.0 for y in ys]
    placed = _spread(wanted, [TEXT * 0.76 + 2 * PAD] * len(wanted), 0.8)
    for want, pos, label, start in zip(wanted, placed, labels, starts):
        cell.segs.append(Seg(start, want, col, want, "ext"))
        if abs(pos - want) < 1e-6:
            cell.texts.append(Txt(col + 0.8, want - 0.38 * TEXT, label, ha="left"))
        else:
            cell.segs.append(Seg(col, want, col + 1.6, pos, "ext"))
            cell.texts.append(Txt(col + 2.4, pos - 0.38 * TEXT, label, ha="left"))
    for x in xs:
        cell.records.append(DimRecord(view, "linear", "position_x", x, 1, fmt(x)))
    for y in ys:
        cell.records.append(DimRecord(view, "linear", "position_y", y, 1, fmt(y)))


def _id_label(cell: Cell, c: Hole, s: float, ident: str) -> None:
    """Hole id just outside the circle, at the first of eight angles that does not hit another id."""
    r = c.d * s / 2
    size = 2.4
    half_w, half_h = (text_width(ident, size) + 2 * PAD) / 2, (0.76 * size + 2 * PAD) / 2
    taken = [t.box for t in cell.texts if t.inside]
    for k in range(8):
        ang = math.radians(45 + 45 * k)
        ux, uy = math.cos(ang), math.sin(ang)
        sx = 0 if abs(ux) < 0.3 else math.copysign(1, ux)
        sy = 0 if abs(uy) < 0.3 else math.copysign(1, uy)
        x = c.x * s + (r + 0.9) * ux + sx * half_w
        y = c.y * s + (r + 0.9) * uy + sy * half_h
        text = Txt(x, y - 0.38 * size, ident, size, ha="center", inside=True)
        if not any(_overlap(text.box, b) for b in taken):
            break
    cell.texts.append(text)


def _callout(cell: Cell, hole: Hole, s: float, text: str) -> None:
    """Leader from one hole of a group to the nearest free spot outside the view."""
    cx, cy, r = hole.x * s, hole.y * s, hole.d * s / 2
    keep_out = (-1.0, -1.0, cell.vw + 1.0, cell.vh + 1.0)
    best = None
    for ring in range(1, 40):
        d = 2.5 * ring
        x0, y0, x1, y1 = -d, -d, cell.vw + d, cell.vh + d
        points = []
        t = x0
        while t <= x1 + 1e-9:
            points += [(t, y0), (t, y1)]
            t += 2.5
        t = y0 + 2.5
        while t < y1 - 1e-9:
            points += [(x0, t), (x1, t)]
            t += 2.5
        for px, py in points:
            box = Txt(px, py - 0.38 * TEXT, text).box
            roomy = (box[0] - CLEAR, box[1] - CLEAR, box[2] + CLEAR, box[3] + CLEAR)
            if _overlap(box, keep_out) or any(_overlap(roomy, t.box) for t in cell.texts if not t.inside):
                continue
            if any(_seg_hits((q.x0, q.y0, q.x1, q.y1), roomy) for q in cell.segs if q.kind != "centre"):
                continue
            # Leader ends on the text box edge nearest the hole.
            tx, ty = min(max(cx, box[0] + PAD), box[2] - PAD), min(max(cy, box[1] + PAD), box[3] - PAD)
            if box[0] < cx < box[2]:
                ty = box[1] if cy < box[1] else box[3]
            elif box[1] < cy < box[3]:
                tx = box[0] if cx < box[0] else box[2]
            length = math.hypot(tx - cx, ty - cy) - r
            if length < ARROW_L + 1.5:
                continue
            if any(_seg_hits((cx, cy, tx, ty), (o.box[0] - 0.8, o.box[1] - 0.8, o.box[2] + 0.8, o.box[3] + 0.8))
                   for o in cell.texts if not o.inside):
                continue
            score = length + 0.3 * d
            if best is None or score < best[0]:
                best = (score, px, py, tx, ty)
        if best:
            break
    if best is None:
        raise BuildError("no free place for a diameter callout; try --sheet A3")
    _, px, py, tx, ty = best
    ux, uy = (tx - cx), (ty - cy)
    norm = math.hypot(ux, uy)
    sx, sy = cx + ux / norm * r, cy + uy / norm * r
    cell.segs.append(Seg(sx, sy, tx, ty, "leader"))
    cell.arrow(sx, sy, -ux, -uy)
    cell.texts.append(Txt(px, py - 0.38 * TEXT, text))


# ---------------------------------------------------------------------------------------------------------------
# sheet layout

@dataclass
class Placed:
    cell: Cell
    ox: float            # sheet position of the view's lower-left corner
    oy: float
    scale: float
    view: ProjectedView
    nts: bool = False


@dataclass
class Sheet:
    part: str
    project: str | None
    material: str | None
    qty: int
    digest: str
    size_name: str
    width: float
    height: float
    scale_name: str
    projection: str
    placed: list[Placed]
    table_rows: list[tuple[str, float, float, float]]
    table_box: Box | None
    title_box: Box
    records: list[tuple[str, DimRecord]] = field(default_factory=list)

    def view_box(self, p: Placed) -> Box:
        return (p.ox, p.oy, p.ox + p.cell.vw, p.oy + p.cell.vh)

    def conflicts(self) -> list[str]:
        """Overlaps that should never happen: text vs text, text vs dimension lines, vs views, vs the title block."""
        found = []
        texts: list[tuple[str, Box]] = []
        for p in self.placed:
            vb = self.view_box(p)
            for t in p.cell.texts:
                b = (t.box[0] + p.ox, t.box[1] + p.oy, t.box[2] + p.ox, t.box[3] + p.oy)
                if not t.inside and _overlap(b, vb):
                    found.append(f"{p.cell.name}: text {t.text!r} overlaps its view")
                for q in p.cell.segs:
                    if q.kind in {"dim", "ext"} and _seg_hits((q.x0 + p.ox, q.y0 + p.oy, q.x1 + p.ox, q.y1 + p.oy), b):
                        found.append(f"{p.cell.name}: text {t.text!r} crossed by a {q.kind} line")
                texts.append((f"{p.cell.name}:{t.text}", b))
            for pts in p.cell.arrows:
                ab = (min(x for x, _ in pts) + p.ox, min(y for _, y in pts) + p.oy,
                      max(x for x, _ in pts) + p.ox, max(y for _, y in pts) + p.oy)
                for t in p.cell.texts:
                    tb = (t.box[0] + p.ox, t.box[1] + p.oy, t.box[2] + p.ox, t.box[3] + p.oy)
                    if _overlap(ab, tb, 1e-3):
                        found.append(f"{p.cell.name}: arrow overlaps text {t.text!r}")
            env = p.cell.envelope()
            eb = (env[0] + p.ox, env[1] + p.oy, env[2] + p.ox, env[3] + p.oy)
            if _overlap(eb, self.title_box):
                found.append(f"{p.cell.name}: overlaps the title block")
            if self.table_box and _overlap(eb, self.table_box):
                found.append(f"{p.cell.name}: overlaps the hole table")
            if eb[0] < FRAME or eb[1] < FRAME or eb[2] > self.width - FRAME or eb[3] > self.height - FRAME:
                found.append(f"{p.cell.name}: leaves the sheet frame")
        for i, (a, ab) in enumerate(texts):
            for b, bb in texts[i + 1:]:
                if _overlap(ab, bb):
                    found.append(f"text overlap: {a} / {b}")
        for i, p in enumerate(self.placed):
            for q in self.placed[i + 1:]:
                pe, qe = p.cell.envelope(), q.cell.envelope()
                if _overlap((pe[0] + p.ox, pe[1] + p.oy, pe[2] + p.ox, pe[3] + p.oy),
                            (qe[0] + q.ox, qe[1] + q.oy, qe[2] + q.ox, qe[3] + q.oy)):
                    found.append(f"views {p.cell.name} and {q.cell.name} overlap")
        return found


def _grid(projection: str) -> dict[str, tuple[int, int]]:
    """(row, col) of each view; row 0 is the top. Third angle: top above front, right of front to its right."""
    if projection == "third":
        return {"top": (0, 0), "isometric": (0, 1), "front": (1, 0), "right": (1, 1)}
    return {"right": (0, 0), "front": (0, 1), "isometric": (1, 0), "top": (1, 1)}


def _table_geometry(n: int, sheet_h: float) -> tuple[int, float, float]:
    """(rows per block, width, height) of a hole table with ``n`` rows."""
    avail = sheet_h - 2 * (FRAME + AREA_PAD) - TITLE_H - AREA_PAD - ROW_H
    per_block = max(int(avail // ROW_H), 4)
    blocks = math.ceil(n / per_block)
    rows = min(n, per_block)
    return per_block, blocks * 62.0 + (blocks - 1) * 3.0, (rows + 1) * ROW_H


def plan_sheet(item: Item, views: dict[str, ProjectedView], projection: str, size_name: str, s: float,
               scale_name: str) -> Sheet | None:
    """Lay the views out at scale ``s`` on one sheet size; None when they do not fit."""
    width, height = SHEETS[size_name]
    ids: dict[str, dict] = {}
    n_rows = 0
    for name in ("front", "top", "right"):
        centres = _centres(views[name].holes)
        if len(centres) >= HOLE_TABLE_MIN:
            ids[name] = {(round(c.x, 2), round(c.y, 2)): f"{name[0].upper()}{i}"
                         for i, c in enumerate(sorted(centres, key=lambda m: (-m.y, m.x)), start=1)}
            n_rows += len(centres)
    table_w = 0.0
    if n_rows:
        _, table_w, table_h = _table_geometry(n_rows, height)
    area = (FRAME + AREA_PAD, FRAME + TITLE_H + AREA_PAD + AREA_PAD, width - FRAME - AREA_PAD - (
        table_w + AREA_PAD if n_rows else 0.0), height - FRAME - AREA_PAD)
    area_w, area_h = area[2] - area[0], area[3] - area[1]
    plan = {"front": ("width", "height"), "top": ("depth",), "right": ()}
    cells = {n: build_cell(views[n], s, plan[n], ids.get(n)) for n in ("front", "top", "right")}
    grid = _grid(projection)
    cols = {0: [], 1: []}
    rows = {0: [], 1: []}
    for n, (r, c) in grid.items():
        if n != "isometric":
            cols[c].append(n)
            rows[r].append(n)
    iso_r, iso_c = grid["isometric"]
    left, bottom, wid, hei, rgt, top = {}, {}, {}, {}, {}, {}
    for n, cell in cells.items():
        env = cell.envelope()
        left[n], bottom[n], rgt[n], top[n] = -env[0], -env[1], env[2] - cell.vw, env[3] - cell.vh
        wid[n], hei[n] = cell.vw, cell.vh
    col_left = {c: max(left[n] for n in cols[c]) for c in (0, 1)}
    col_view = {c: max(wid[n] + rgt[n] for n in cols[c]) for c in (0, 1)}
    row_bottom = {r: max(bottom[n] for n in rows[r]) for r in (0, 1)}
    row_view = {r: max(hei[n] + top[n] for n in rows[r]) for r in (0, 1)}
    col_w = {c: col_left[c] + col_view[c] for c in (0, 1)}
    row_h = {r: row_bottom[r] + row_view[r] for r in (0, 1)}
    col_w[iso_c] = max(col_w[iso_c], 48.0)
    row_h[iso_r] = max(row_h[iso_r], 40.0)
    total_w, total_h = col_w[0] + col_w[1] + CELL_GAP, row_h[0] + row_h[1] + CELL_GAP
    if total_w > area_w or total_h > area_h:
        return None
    ox = area[0] + (area_w - total_w) / 2
    oy = area[1] + (area_h - total_h) / 2
    col_x = {0: ox, 1: ox + col_w[0] + CELL_GAP}
    row_y = {1: oy, 0: oy + row_h[1] + CELL_GAP}        # row 0 is the upper row
    placed = []
    for n, (r, c) in grid.items():
        if n == "isometric":
            continue
        placed.append(Placed(cells[n], col_x[c] + col_left[c], row_y[r] + row_bottom[r], s, views[n]))
    # Isometric view: same scale if it fits the free corner, otherwise scaled down and marked as such.
    iso = views["isometric"]
    iw, ih = iso.size
    free_w, free_h = col_w[iso_c] - 2 * 4.0, row_h[iso_r] - 2 * 4.0 - 2 * SMALL
    s_iso = min(s, free_w / iw, free_h / ih)
    nts = s_iso < s - 1e-9
    icell = Cell("isometric", iw * s_iso, ih * s_iso)
    label = LABELS["isometric"] + (" (NOT TO SCALE)" if nts else "")
    icell.texts.append(Txt(icell.vw / 2, -1.2 - 0.76 * SMALL, label, SMALL))
    cx = col_x[iso_c] + (col_w[iso_c] - icell.vw) / 2
    cy = row_y[iso_r] + (row_h[iso_r] - icell.vh) / 2 + SMALL
    placed.append(Placed(icell, cx, cy, s_iso, iso, nts))
    title = (width - FRAME - TITLE_W, FRAME, width - FRAME, FRAME + TITLE_H)
    sheet = Sheet(item.name, item.project, item.material, item.qty, item.digest, size_name, width, height,
                  scale_name, projection, placed, [], None, title)
    if n_rows:
        rows_list = []
        for n in ("front", "top", "right"):
            rows_list += cells[n].table
        rows_list.sort(key=lambda r: ("ftr".index(r[0][0].lower()), int(r[0][1:])))
        sheet.table_rows = rows_list
        x1 = width - FRAME - AREA_PAD
        sheet.table_box = (x1 - table_w, height - FRAME - AREA_PAD - table_h, x1, height - FRAME - AREA_PAD)
    return sheet


def choose_sheet(item: Item, views: dict[str, ProjectedView], projection: str, sheet: str) -> Sheet:
    """Largest standard scale that fits. ``auto``: A4, unless A3 allows a larger standard scale."""
    def fit(size_name: str) -> Sheet | None:
        vw = {n: views[n].size for n in ("front", "top", "right")}
        for s, name in SCALES:
            # Cheap necessary condition before building any dimensions.
            gx = vw["front"][0] + vw["right"][0] if projection == "third" else vw["right"][0] + vw["front"][0]
            gy = vw["front"][1] + vw["top"][1] if projection == "third" else vw["right"][1] + vw["top"][1]
            if gx * s > SHEETS[size_name][0] or gy * s > SHEETS[size_name][1]:
                continue
            placed = plan_sheet(item, views, projection, size_name, s, name)
            if placed:
                return placed
        return None

    if sheet != "auto":
        found = fit(sheet)
        if not found:
            raise ConfigError(f"{item.name}: does not fit {sheet} even at 1:50; pass --sheet A3 or tabloid")
        return found
    a4, a3 = fit("A4"), fit("A3")
    found = a3 if a3 and (not a4 or _scale_value(a3.scale_name) > _scale_value(a4.scale_name)) else a4
    if not found:
        raise ConfigError(f"{item.name}: does not fit A3 even at 1:50; pass --sheet tabloid")
    return found


def _scale_value(name: str) -> float:
    a, b = name.split(":")
    return float(a) / float(b)


# ---------------------------------------------------------------------------------------------------------------
# rendering

def _clip(text: str, size: float, width: float) -> str:
    if text_width(text, size) <= width:
        return text
    while len(text) > 1 and text_width(text + "\u2026", size) > width:
        text = text[:-1]
    return text + "\u2026"


def render_sheet(sheet: Sheet, views: dict[str, ProjectedView], files: dict[str, Path]) -> None:
    """Draw a planned sheet with matplotlib and write the requested files."""
    import matplotlib
    from matplotlib.collections import LineCollection, PolyCollection
    from matplotlib.figure import Figure

    W, H = sheet.width, sheet.height
    font = {"family": "DejaVu Sans"}

    def build(fig: Figure) -> None:
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.axis("off")
        groups: dict[str, list] = {}

        def seg(kind, x0, y0, x1, y1):
            groups.setdefault(kind, []).append([(x0, y0), (x1, y1)])

        def rect(kind, b):
            for a, c in (((b[0], b[1]), (b[2], b[1])), ((b[2], b[1]), (b[2], b[3])), ((b[2], b[3]), (b[0], b[3])),
                         ((b[0], b[3]), (b[0], b[1]))):
                seg(kind, a[0], a[1], c[0], c[1])

        rect("frame", (FRAME, FRAME, W - FRAME, H - FRAME))
        texts: list[tuple] = []
        _title_block(sheet, seg, rect, texts)
        arrows: list = []
        for p in sheet.placed:
            view = p.view
            defl = 0.03 / max(p.scale, 1e-3)
            for kind, edges in (("hidden", view.hidden), ("tangent", view.tangent), ("visible", view.visible)):
                if p.cell.name == "isometric" and kind == "hidden":
                    continue
                for edge in edges:
                    pts = [((x - view.bounds[0]) * p.scale + p.ox, (y - view.bounds[1]) * p.scale + p.oy)
                           for x, y in _sample(edge, defl)]
                    groups.setdefault(kind, []).append(pts)
            for q in p.cell.segs:
                seg(q.kind, q.x0 + p.ox, q.y0 + p.oy, q.x1 + p.ox, q.y1 + p.oy)
            for pts in p.cell.arrows:
                arrows.append([(x + p.ox, y + p.oy) for x, y in pts])
            for t in p.cell.texts:
                texts.append((t.x + p.ox, t.y + p.oy, t.text, t.size, t.rot, t.ha))
        _hole_table(sheet, seg, rect, texts)
        styles = {"hidden": (0, (6.8, 2.8)), "centre": (0, (9.0, 2.0, 2.0, 2.0))}
        for kind in ("hidden", "tangent", "visible", "centre", "ext", "dim", "leader", "title", "frame"):
            if kind in groups:
                ax.add_collection(LineCollection(groups[kind], colors="black", linewidths=LW[kind] * MM_PT,
                                                 linestyles=styles.get(kind, "solid"), capstyle="butt",
                                                 joinstyle="round"))
        if arrows:
            ax.add_collection(PolyCollection(arrows, facecolors="black", edgecolors="black", linewidths=0.2))
        for x, y, s, size, rot, ha in texts:
            ax.text(x, y, s, fontsize=size * MM_PT, rotation=rot, rotation_mode="anchor", ha=ha, va="baseline",
                    color="black", **font)

    rc = {"svg.hashsalt": "agentcad", "svg.fonttype": "none", "pdf.fonttype": 42, "lines.scale_dashes": False,
          "path.simplify": False, "text.antialiased": True}
    with matplotlib.rc_context(rc):
        for fmt_name, path in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if fmt_name == "png":
                # Round the figure to whole pixels so the PNG is exactly the sheet at 150 dpi.
                fig = Figure(figsize=(png_size(sheet.size_name)[0] / DPI, png_size(sheet.size_name)[1] / DPI), dpi=DPI)
                meta = {"Software": f"agentcad {__version__}"}
            elif fmt_name == "svg":
                fig = Figure(figsize=(W / 25.4, H / 25.4), dpi=DPI)
                meta = {"Creator": f"agentcad {__version__}", "Date": None, "Title": sheet.part}
            else:
                fig = Figure(figsize=(W / 25.4, H / 25.4), dpi=DPI)
                meta = {"Creator": f"agentcad {__version__}", "Producer": f"agentcad {__version__}",
                        "CreationDate": None, "Title": sheet.part}
            fig.patch.set_facecolor("white")
            build(fig)
            fig.savefig(str(path), format=fmt_name, dpi=DPI, metadata=meta, facecolor="white")


def png_size(size_name: str) -> tuple[int, int]:
    """Pixel size of the PNG for a sheet at 150 dpi."""
    w, h = SHEETS[size_name]
    return round(w / 25.4 * DPI), round(h / 25.4 * DPI)


def _title_block(sheet: Sheet, seg, rect, texts) -> None:
    x0, y0, x1, y1 = sheet.title_box
    rect("title", sheet.title_box)
    row = 9.0
    foot = y0 + 6.0
    ys = [foot, foot + row, foot + 2 * row, y1]
    for y in ys[:-1]:
        seg("title", x0, y, x1, y)
    cells = [
        (ys[2], ys[3], [(x0, x0 + 95, "PART", sheet.part), (x0 + 95, x1, "QTY", str(sheet.qty))]),
        (ys[1], ys[2], [(x0, x0 + 65, "PROJECT", sheet.project or "-"),
                        (x0 + 65, x1, "MATERIAL", sheet.material or "-")]),
        (ys[0], ys[1], [(x0, x0 + 30, "SCALE", sheet.scale_name),
                        (x0 + 30, x0 + 80, "PROJECTION", f"{sheet.projection.upper()} ANGLE"),
                        (x0 + 80, x0 + 105, "SHEET", sheet.size_name.upper()), (x0 + 105, x1, "UNITS", "mm")]),
    ]
    for lo, hi, items in cells:
        for a, b, label, value in items:
            if a > x0:
                seg("title", a, lo, a, hi)
            texts.append((a + 1.2, hi - 1.2 - 0.76 * SMALL, label, SMALL * 0.85, 0, "left"))
            texts.append((a + 1.2, lo + 1.7, _clip(value, TEXT, b - a - 2.4), TEXT, 0, "left"))
    texts.append((x0 + 1.2, y0 + 1.6, f"agentcad {__version__}", SMALL, 0, "left"))
    texts.append((x1 - 1.2, y0 + 1.6, f"GEOMETRY SHA256 {sheet.digest}", SMALL, 0, "right"))


def _hole_table(sheet: Sheet, seg, rect, texts) -> None:
    if not sheet.table_box or not sheet.table_rows:
        return
    x0, _, _, y1 = sheet.table_box
    per_block = max(int((sheet.height - 2 * (FRAME + AREA_PAD) - TITLE_H - AREA_PAD - ROW_H) // ROW_H), 4)
    cols = (12.0, 17.0, 17.0, 16.0)
    for b in range(math.ceil(len(sheet.table_rows) / per_block)):
        rows = sheet.table_rows[b * per_block:(b + 1) * per_block]
        bx = x0 + b * 65.0
        by1 = y1
        rect("title", (bx, by1 - (len(rows) + 1) * ROW_H, bx + sum(cols), by1))
        for i in range(1, len(rows) + 1):
            seg("title", bx, by1 - i * ROW_H, bx + sum(cols), by1 - i * ROW_H)
        edge = bx
        for width in cols[:-1]:
            edge += width
            seg("title", edge, by1, edge, by1 - (len(rows) + 1) * ROW_H)
        for i, row in enumerate([("ID", "X", "Y", "Ø"), *rows]):
            base = by1 - (i + 1) * ROW_H + 1.2
            values = [row[0], *(fmt(v) if i else v for v in row[1:])] if i else list(row)
            edge = bx
            for width, value in zip(cols, values):
                texts.append((edge + 1.2, base, str(value), 2.8, 0, "left"))
                edge += width


# ---------------------------------------------------------------------------------------------------------------
# inputs and the public entry points

@dataclass
class Item:
    name: str
    shape: cq.Shape
    project: str | None
    material: str | None
    qty: int
    digest: str = ""


def _collect(inputs: list[str], parts: list[str] | None) -> tuple[str | None, Path | None, list[Item]]:
    """(project name, project folder, items) for a project folder or a list of STEP files."""
    if not inputs:
        inputs = ["."]
    paths = [Path(p) for p in inputs]
    is_project = [(p / "agentcad.toml").is_file() or (p.is_file() and p.name == "agentcad.toml") for p in paths]
    if any(is_project):
        if len(paths) != 1:
            raise ConfigError("give one project folder or STEP files, not both or several projects")
        project = Project.load(paths[0])
        items = []
        for name, part in project.parts.items():
            if parts and name not in parts:
                continue
            try:
                shape = part.solid("model")
            except (ValueError, TypeError) as exc:
                raise BuildError(f"part {name}: {exc}") from exc
            items.append(Item(name, shape, project.name, part.material, part.qty))
        valid = list(project.parts)
        _check_parts(parts, valid)
        return project.name, project.path, items
    items = []
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in MESH_SUFFIXES:
            raise ConfigError("drawings need STEP files or a project (meshes have no edges)")
        if path.is_dir():
            raise ConfigError(f"{path} is not an agentcad project (no agentcad.toml); pass a project folder or STEP files")
        if suffix not in {".step", ".stp"}:
            raise ConfigError(f"Unsupported file {path.name}: drawings need STEP files or a project")
        if not path.is_file():
            raise ConfigError(f"File not found: {path}")
        try:
            imported = cq.importers.importStep(str(path))
            shape = imported.val() if len(imported.vals()) == 1 else cq.Compound.makeCompound(imported.vals())
        except Exception as exc:  # OCCT raises bare Standard_Failure for unreadable STEP files
            raise ConfigError(f"Cannot read {path.name}: {type(exc).__name__}: {exc}") from exc
        items.append(Item(path.stem, shape, None, None, 1))
    valid = [i.name for i in items]
    _check_parts(parts, valid)
    items = [i for i in items if not parts or i.name in parts]
    if len({i.name for i in items}) != len(items):
        raise ConfigError("two input files have the same name; drawings are written as <name>.svg")
    return None, None, items


def _check_parts(parts: list[str] | None, valid: list[str]) -> None:
    unknown = [p for p in parts or [] if p not in valid]
    if unknown:
        raise ConfigError(f"Unknown part {unknown[0]!r}; valid parts: {', '.join(valid)}")


def _as_list(value, name: str) -> list[str] | None:
    if value is None:
        return None
    items = [v.strip() for v in value.split(",")] if isinstance(value, str) else [str(v).strip() for v in value]
    items = [v for v in items if v]
    if not items:
        raise ConfigError(f"{name} is empty")
    return items


def make_drawings(inputs, out=None, parts=None, formats=FORMATS, projection: str = "third",
                  sheet: str = "auto") -> dict:
    """Write one dimensioned sheet per part and return the ``agentcad.drawing/1`` document."""
    if isinstance(inputs, (str, Path)):
        inputs = [inputs]
    if projection not in {"third", "first"}:
        raise ConfigError("projection must be 'third' or 'first'")
    if sheet != "auto" and sheet not in SHEETS:
        raise ConfigError(f"sheet must be auto, {', '.join(SHEETS)}")
    wanted = _as_list(formats, "format") or list(FORMATS)
    bad = [f for f in wanted if f not in FORMATS]
    if bad:
        raise ConfigError(f"Unknown format {bad[0]!r}; use any of {', '.join(FORMATS)}")
    project_name, folder, items = _collect([str(i) for i in inputs], _as_list(parts, "parts"))
    target = Path(out) if out else (folder / "build" / "drawings" if folder else Path("drawings"))
    target = target.resolve()
    sheets = []
    for item in items:
        if item.shape.isNull() or item.shape.Volume() <= 0:
            raise BuildError(f"part {item.name}: no valid solid to draw")
        item.digest = geometry_digest(item.shape)
        views = project_views(item.shape)
        planned = choose_sheet(item, views, projection, sheet)
        files = {f: target / f"{item.name}.{f}" for f in FORMATS if f in wanted}
        render_sheet(planned, views, files)
        sheets.append(_sheet_doc(planned, files))
    return {"schema": SCHEMA, "project": project_name, "out": str(target), "sheets": sheets}


def _sheet_doc(sheet: Sheet, files: dict[str, Path]) -> dict:
    views, dims = [], []
    for p in sheet.placed:
        box = sheet.view_box(p)
        views.append({"name": p.cell.name, "direction": p.view.sight,
                      "bbox_mm": {"min_mm": [round(box[0], 3), round(sheet.height - box[3], 3)],
                                  "max_mm": [round(box[2], 3), round(sheet.height - box[1], 3)],
                                  "size_mm": [round(box[2] - box[0], 3), round(box[3] - box[1], 3)]},
                      "scale": "NTS" if p.nts else sheet.scale_name})
        for rec in p.cell.records:
            dims.append({"id": f"D{len(dims) + 1}", "view": rec.view, "kind": rec.kind, "role": rec.role,
                         "value_mm": round(rec.value, 4), "count": rec.count, "text": rec.text})
    doc = {"part": sheet.part, "files": {k: str(v) for k, v in files.items()}, "sheet": sheet.size_name,
           "scale": sheet.scale_name, "projection": sheet.projection, "views": views, "dimensions": dims}
    if sheet.table_rows:
        doc["hole_table"] = [{"id": i, "x_mm": round(x, 4), "y_mm": round(y, 4), "diameter_mm": round(d, 4)}
                             for i, x, y, d in sheet.table_rows]
    return doc


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad drawing``."""
    doc = make_drawings(list(args.inputs) or ["."], out=args.out, parts=args.parts, formats=args.format,
                        projection=args.projection, sheet=args.sheet)
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        for entry in doc["sheets"]:
            for path in entry["files"].values():
                print(path)
    return 0
