"""Print-frame mesh measurements for unsupported surfaces and bed placement."""
from __future__ import annotations

import numpy as np
import trimesh

from .geometry import bbox


def overhang(mesh: trimesh.Trimesh, max_deg: float = 45, bed_tol: float = 0.05,
             first_layer_mm: float = 0.3) -> dict:
    """Group downward unsupported faces into shared-edge connected regions.

    A face is unsupported when it faces down and its tilt from vertical exceeds ``max_deg`` by more than 0.01 deg
    (so a face exactly at the limit passes). Faces on the bed, and downward faces whose centroid lies below
    ``first_layer_mm``, are ignored: a bottom-edge chamfer prints as part of the first layers. The ignored
    area is reported as ``first_layer_area_mm2``.
    """
    if not 0 <= max_deg <= 90:
        raise ValueError("Overhang angle must be between 0 and 90 degrees")
    on_bed = np.all(np.abs(mesh.triangles[:, :, 2]) <= bed_tol, axis=1)
    # Tilt from vertical of a downward face: 0 for a wall, 90 for a flat ceiling.
    tilt = np.degrees(np.arcsin(np.clip(-mesh.face_normals[:, 2], -1.0, 1.0)))
    steep = (tilt > max_deg + 0.01) & ~on_bed
    in_first_layer = steep & (mesh.triangles_center[:, 2] < first_layer_mm)
    unsupported = steep & ~in_first_layer
    indices = np.flatnonzero(unsupported)
    edges = mesh.face_adjacency[np.all(unsupported[mesh.face_adjacency], axis=1)]
    components = trimesh.graph.connected_components(edges, nodes=indices, min_len=1)
    regions = []
    for component in components:
        weights = mesh.area_faces[component]
        z = mesh.triangles[component, :, 2]
        regions.append({"area_mm2": float(weights.sum()), "z_mm": [float(z.min()), float(z.max())],
                        "centroid_mm": np.average(mesh.triangles_center[component], axis=0, weights=weights).tolist()})
    regions.sort(key=lambda r: r["area_mm2"], reverse=True)
    return {"area_mm2": float(mesh.area_faces[indices].sum()), "region_count": len(regions),
            "regions": regions[:5], "first_layer_area_mm2": float(mesh.area_faces[in_first_layer].sum())}


def bed_fit(mesh_or_bbox: trimesh.Trimesh | dict, bed_xyz: tuple[float, float, float],
            margin: float = 2.0) -> dict:
    """Test both orthogonal XY placements, including the height limit."""
    bounds = mesh_or_bbox if isinstance(mesh_or_bbox, dict) else bbox(mesh_or_bbox)
    x, y, z = bounds["size_mm"]
    bx, by, bz = bed_xyz
    if margin < 0 or min(bed_xyz) <= 0:
        raise ValueError("Bed dimensions must be positive and margin nonnegative")
    direct = x + 2 * margin <= bx and y + 2 * margin <= by and z <= bz
    rotated = y + 2 * margin <= bx and x + 2 * margin <= by and z <= bz
    return {"fits": bool(direct or rotated), "rotation_z_deg": 0 if direct or not rotated else 90,
            "size_mm": [x, y, z], "bed_mm": list(bed_xyz), "margin_mm": margin}


def bed_contact(mesh: trimesh.Trimesh, bed_tol: float = 0.05) -> float:
    """Return area of triangles whose vertices all lie on the bed."""
    return float(mesh.area_faces[np.all(np.abs(mesh.triangles[:, :, 2]) <= bed_tol, axis=1)].sum())
