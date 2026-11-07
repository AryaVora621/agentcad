"""Pairwise measurements between solids: overlap, clearance, cross-sections and motion."""
from __future__ import annotations

from collections.abc import Sequence

import cadquery as cq
import numpy as np
import trimesh
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from .geometry import Geometry, bbox, to_mesh, to_shape

Vector3 = Sequence[float]


def _disjoint(a: Geometry, b: Geometry) -> bool:
    """True when bounding boxes only touch or miss, so no volume can be shared."""
    ba, bb = bbox(a), bbox(b)
    return any(ba["max_mm"][i] <= bb["min_mm"][i] or bb["max_mm"][i] <= ba["min_mm"][i] for i in range(3))


def common_volume(common: cq.Shape) -> float:
    """Volume of the solids in a boolean result. Touching faces can yield a shell with a bogus volume."""
    return max(0.0, float(sum(solid.Volume() for solid in common.Solids())))


def _is_clean(common: cq.Shape) -> bool:
    """True when every face of a boolean result belongs to one of its solids (no shell or face residue)."""
    solids = common.Solids()
    return (len(common.Faces()) == sum(len(s.Faces()) for s in solids)
            and len(common.Shells()) == sum(len(s.Shells()) for s in solids))


def _brep_common(a: cq.Shape, b: cq.Shape) -> cq.Shape:
    return a.intersect(b)


def overlap(a: Geometry, b: Geometry) -> dict:
    """Overlap volume (mm3), centroid and bounding box of two geometries, as ``{"volume_mm3", "centroid_mm", "bbox"}``.

    OCC's common is order dependent when faces are coplanar (a lid resting on a rim can read 0 one way and
    the true volume the other). Both orders are computed; if either leaves residue outside solids or the two
    disagree, the tessellated meshes are intersected too and the largest volume wins, so a doubtful
    result errs toward reporting an overlap.
    """
    empty = {"volume_mm3": 0.0, "centroid_mm": None, "bbox": None}
    if _disjoint(a, b):
        return empty
    candidates: list[tuple[float, cq.Shape | trimesh.Trimesh]] = []
    if isinstance(a, cq.Shape) and isinstance(b, cq.Shape):
        ab, ba = _brep_common(a, b), _brep_common(b, a)
        v_ab, v_ba = common_volume(ab), common_volume(ba)
        if _is_clean(ab) and _is_clean(ba) and abs(v_ab - v_ba) <= max(1e-6, 1e-4 * max(v_ab, v_ba)):
            return _describe(v_ab, ab) if v_ab > 0 else empty
        candidates = [(v_ab, ab), (v_ba, ba)]
    # trimesh divides by a zero volume while handling an empty intersection; that case is handled below.
    with np.errstate(all="ignore"):
        mesh_a = to_mesh(a) if isinstance(a, cq.Shape) else a
        mesh_b = to_mesh(b) if isinstance(b, cq.Shape) else b
        common = trimesh.boolean.intersection([mesh_a, mesh_b], engine="manifold")
        if len(common.faces):
            candidates.append((max(0.0, float(common.volume)), common))
    volume, shape = max(candidates, key=lambda c: c[0], default=(0.0, None))
    return _describe(volume, shape) if volume > 0 else empty


def _describe(volume: float, shape: cq.Shape | trimesh.Trimesh) -> dict:
    if isinstance(shape, trimesh.Trimesh):
        centroid = shape.center_mass.tolist()
    else:
        centroid = [float(v) for v in shape.Solids()[0].Center().toTuple()] if len(shape.Solids()) == 1 else [
            float(v) for v in cq.Compound.makeCompound(shape.Solids()).Center().toTuple()]
    return {"volume_mm3": volume, "centroid_mm": centroid, "bbox": bbox(shape)}


def interference(a: Geometry, b: Geometry) -> float:
    """Return the overlap volume in mm3. Faces that only touch share no volume and give 0."""
    return overlap(a, b)["volume_mm3"]


def distance(a: cq.Shape, b: cq.Shape) -> float:
    """Return the minimum distance in mm between two solids; 0 when they touch or overlap."""
    a, b = to_shape(a), to_shape(b)
    if interference(a, b) > 0:
        return 0.0
    probe = BRepExtrema_DistShapeShape(a.wrapped, b.wrapped)
    if not probe.IsDone():
        raise ValueError("Minimum distance computation failed")
    return float(probe.Value())


def section(shapes: Sequence[cq.Shape] | cq.Shape, axis: str = "z", at: float = 0.0) -> dict:
    """Cut each shape with the plane ``axis = at`` and total the cut faces; holes are subtracted.

    Areas are summed per shape, so solids that overlap at the cut count twice. Fusing them first is avoided on
    purpose: OCC fails to fuse solids that merely touch, which is the normal state of mating parts.
    """
    if axis not in ("x", "y", "z"):
        raise ValueError("axis must be 'x', 'y' or 'z'")
    items = [shapes] if isinstance(shapes, cq.Shape) else list(shapes)
    if not items:
        raise ValueError("section needs at least one shape")
    normal = tuple(1.0 if name == axis else 0.0 for name in "xyz")
    plane = cq.Plane(origin=tuple(at * n for n in normal), normal=normal)
    faces, loops = [], 0
    for shape in items:
        wires = cq.Workplane(plane).newObject([shape]).section().wires().vals()
        if wires:
            faces += cq.occ_impl.shapes.wiresToFaces(wires)
            loops += len(wires)
    if not faces:
        return {"area_mm2": 0.0, "bbox_mm": None, "loops": 0}
    cut = cq.Compound.makeCompound(faces)
    return {"area_mm2": float(sum(f.Area() for f in faces)), "bbox_mm": bbox(cut), "loops": loops}


def unit_direction(direction: Vector3) -> np.ndarray:
    """Normalise a 3-vector, rejecting zero and non-finite input."""
    vector = np.asarray(direction, dtype=float)
    if vector.shape != (3,) or not np.isfinite(vector).all() or not np.linalg.norm(vector):
        raise ValueError("direction must be a finite, non-zero (x, y, z) tuple")
    return vector / np.linalg.norm(vector)


def _overlap_against(moved: cq.Shape, against: Sequence[cq.Shape]) -> float:
    return sum(interference(moved, other) for other in against)


def slide(moving: cq.Shape, against: Sequence[cq.Shape] | cq.Shape, direction: Vector3, distance: float,
          steps: int = 40) -> list[dict]:
    """Translate ``moving`` from 0 to ``distance`` mm along ``direction`` and measure overlap at each step.

    The overlap is summed over the ``against`` solids. Returns ``steps + 1`` records
    ``{"offset_mm", "overlap_mm3"}``.
    """
    if steps < 1 or distance < 0:
        raise ValueError("steps must be at least 1 and distance must not be negative")
    vector = unit_direction(direction)
    fixed = [against] if isinstance(against, cq.Shape) else list(against)
    return [{"offset_mm": float(d), "overlap_mm3": _overlap_against(moving.translate(tuple(vector * d)), fixed)}
            for d in np.linspace(0.0, distance, steps + 1)]


def play(moving: cq.Shape, against: Sequence[cq.Shape] | cq.Shape, direction: Vector3, max_mm: float = 5.0,
         resolution_mm: float = 0.01, tol_mm3: float = 0.01) -> float | None:
    """Free travel of ``moving`` along ``direction`` before overlap exceeds ``tol_mm3``.

    Returns 0.0 when it already overlaps and None when it never touches within ``max_mm``.
    """
    if max_mm <= 0 or resolution_mm <= 0:
        raise ValueError("max_mm and resolution_mm must be positive")
    vector = unit_direction(direction)
    fixed = [against] if isinstance(against, cq.Shape) else list(against)

    def blocked(offset: float) -> bool:
        return _overlap_against(moving.translate(tuple(vector * offset)), fixed) > tol_mm3

    if blocked(0.0):
        return 0.0
    # A coarse scan brackets the first contact cheaply; bisection then refines it.
    coarse = max(resolution_mm, max_mm / 100)
    free = 0.0
    for offset in np.arange(coarse, max_mm + coarse / 2, coarse):
        offset = min(float(offset), max_mm)
        if blocked(offset):
            low, high = free, offset
            while high - low > resolution_mm / 4:
                middle = (low + high) / 2
                low, high = (low, middle) if blocked(middle) else (middle, high)
            return (low + high) / 2
        free = offset
    return None
