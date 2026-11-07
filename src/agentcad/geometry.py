"""Measured properties and conversions for BRep solids and triangle meshes."""
from __future__ import annotations

import tempfile
from pathlib import Path

import cadquery as cq
import numpy as np
import trimesh
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

Geometry = cq.Shape | trimesh.Trimesh


def to_shape(obj: cq.Workplane | cq.Shape) -> cq.Shape:
    """Preserve every shape on a Workplane stack, not only its first value."""
    if isinstance(obj, cq.Shape):
        return obj
    if isinstance(obj, cq.Workplane):
        shapes = [value for value in obj.vals() if isinstance(value, cq.Shape)]
        if not shapes:
            raise ValueError("Workplane contains no shapes")
        return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    raise TypeError(f"Expected Shape or Workplane, got {type(obj).__name__}")


def place(shape: cq.Shape, pose: object) -> cq.Shape:
    """Return geometry moved by a Pose or CadQuery Location."""
    location = pose.location() if hasattr(pose, "location") else pose
    return shape.moved(location)


def bbox(shape: Geometry) -> dict:
    """Return the measured axis-aligned bounding box in millimetres."""
    if isinstance(shape, trimesh.Trimesh):
        low, high = shape.bounds
    else:
        # Exact bounds: CadQuery's BoundingBox() widens by tessellation deflection once a shape has been exported.
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
        x0, y0, z0, x1, y1, z1 = box.Get()
        low, high = np.array([x0, y0, z0]), np.array([x1, y1, z1])
    return {"min_mm": np.asarray(low, float).tolist(), "max_mm": np.asarray(high, float).tolist(),
            "size_mm": np.asarray(high - low, float).tolist()}


def volume(shape: Geometry) -> float:
    """Return volume in cubic millimetres."""
    return float(shape.volume if isinstance(shape, trimesh.Trimesh) else shape.Volume())


def area(shape: Geometry) -> float:
    """Return surface area in square millimetres."""
    return float(shape.area if isinstance(shape, trimesh.Trimesh) else shape.Area())


def center_of_mass(shape: Geometry) -> list[float]:
    """Return the volume centre of mass in millimetres."""
    return [float(v) for v in (shape.center_mass if isinstance(shape, trimesh.Trimesh) else shape.Center().toTuple())]


def solids_count(shape: Geometry) -> int:
    """Count disconnected solids or mesh components."""
    return len(shape.split(only_watertight=False)) if isinstance(shape, trimesh.Trimesh) else len(shape.Solids())


def is_valid(shape: Geometry) -> bool:
    """Return whether geometry forms a valid, positive-volume solid."""
    valid = shape.is_watertight and shape.is_winding_consistent if isinstance(shape, trimesh.Trimesh) else shape.isValid()
    return bool(valid and solids_count(shape) > 0 and volume(shape) > 0)


def to_mesh(shape: cq.Shape, tolerance: float = 0.02, angular_tolerance: float = 0.05) -> trimesh.Trimesh:
    """Tessellate through STL with explicit linear and angular deflection."""
    with tempfile.TemporaryDirectory(prefix="agentcad-mesh-") as directory:
        path = str(Path(directory) / "part.stl")
        cq.exporters.export(shape, path, tolerance=tolerance, angularTolerance=angular_tolerance)
        return trimesh.load(path, force="mesh")


def load(path: str | Path) -> Geometry:
    """Load STEP as BRep or a supported mesh format as a Trimesh."""
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    if path.suffix.lower() in {".step", ".stp"}:
        return to_shape(cq.importers.importStep(str(path)))
    if path.suffix.lower() not in {".stl", ".obj", ".ply", ".3mf", ".glb"}:
        raise ValueError(f"Unsupported geometry format: {path.suffix}")
    return trimesh.load(str(path), force="mesh")
