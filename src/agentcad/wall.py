"""Deterministic inward ray wall measurements, ported from stlwall."""
from __future__ import annotations

import numpy as np
import trimesh


def wall_thickness(mesh: trimesh.Trimesh, samples: int = 60000, seed: int = 0,
                   thin: float | None = None, zmin: float | None = None, zmax: float | None = None,
                   *, bands: float = 0.0, up: bool = False, rmin: float | None = None,
                   rmax: float | None = None) -> dict:
    """Measure inward rays, excluding sub-0.05 mm grazing hits.

    As in stlwall, distances start 0.01 mm inside the sampled surface. This
    intentional offset avoids self-hits and slightly understates thickness.
    """
    if samples <= 0 or bands < 0:
        raise ValueError("Samples must be positive and bands nonnegative")
    points, face_indices = trimesh.sample.sample_surface_even(mesh, samples, seed=seed)
    normals = mesh.face_normals[face_indices]
    keep = np.ones(len(points), dtype=bool)
    if up:
        keep &= normals[:, 2] > 0.3
    if zmin is not None:
        keep &= points[:, 2] >= zmin
    if zmax is not None:
        keep &= points[:, 2] <= zmax
    centre = mesh.bounds.mean(axis=0)[:2]
    radii = np.linalg.norm(points[:, :2] - centre, axis=1)
    if rmin is not None:
        keep &= radii >= rmin
    if rmax is not None:
        keep &= radii <= rmax
    points, normals = points[keep], normals[keep]
    distances, locations = [], []
    # Bound ray batches so large meshes do not allocate an unbounded candidate array.
    for start in range(0, len(points), 2048):
        p, n = points[start:start + 2048], normals[start:start + 2048]
        origins = p - n * 0.01
        hits, rays, _ = mesh.ray.intersects_location(origins, -n, multiple_hits=False)
        d = np.linalg.norm(hits - origins[rays], axis=1)
        good = d >= 0.05
        distances.extend(d[good])
        locations.extend(p[rays[good]])
    d = np.asarray(distances)
    result = {"samples": len(points), "hits": len(d), "min_mm": None, "p1_mm": None,
              "p5_mm": None, "median_mm": None, "min_at_mm": None, "thin_mm": thin, "thin_share": None,
              "thin_z_mm": None, "thin_radius_mm": None, "under_1_mm_share": None, "bands": []}
    if not len(d):
        return result
    p = np.asarray(locations)
    z, radii = p[:, 2], np.linalg.norm(p[:, :2] - centre, axis=1)
    result.update(min_mm=float(d.min()), p1_mm=float(np.percentile(d, 1)),
                  p5_mm=float(np.percentile(d, 5)), median_mm=float(np.median(d)),
                  min_at_mm=p[int(d.argmin())].tolist(), under_1_mm_share=float((d < 1).mean()))
    if thin is not None:
        selected = d < thin
        result["thin_share"] = float(selected.mean())
        if selected.any():
            result["thin_z_mm"] = [float(z[selected].min()), float(z[selected].max())]
            result["thin_radius_mm"] = [float(radii[selected].min()), float(radii[selected].max())]
    if bands > 0:
        for low in np.arange(np.floor(z.min()), z.max(), bands):
            selected = (z >= low) & (z < low + bands)
            if selected.any():
                result["bands"].append({"z_mm": [float(low), float(low + bands)],
                                        "min_mm": float(d[selected].min()),
                                        "p2_mm": float(np.percentile(d[selected], 2)),
                                        "median_mm": float(np.median(d[selected])), "samples": int(selected.sum())})
    return result
