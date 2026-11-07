"""Compare two build manifests: which parts appeared, vanished or changed shape."""
from __future__ import annotations

import json
from pathlib import Path

from .report import json_value

SCHEMA = "agentcad.diff/1"
# Differences below these are numeric noise from re-tessellation or float formatting.
VOLUME_EPS_MM3 = 1e-3
SIZE_EPS_MM = 1e-3


def load_manifest(path: str | Path) -> dict:
    """Read manifest.json from a file path, a build folder or a project folder with a build/."""
    path = Path(path)
    if path.is_dir():
        path = path / "manifest.json" if (path / "manifest.json").exists() else path / "build" / "manifest.json"
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read manifest {path}: {exc}") from exc
    if manifest.get("schema") != "agentcad.manifest/1":
        raise ValueError(f"{path} is not an agentcad.manifest/1 file")
    return manifest


def diff_manifests(old: dict, new: dict) -> dict:
    """Return a ``agentcad.diff/1`` document describing what changed between two manifests."""
    old_parts = {p["name"]: p for p in old["parts"]}
    new_parts = {p["name"]: p for p in new["parts"]}
    changed = []
    for name in sorted(old_parts.keys() & new_parts.keys()):
        entry = _compare(old_parts[name], new_parts[name])
        if entry:
            changed.append({"name": name, **entry})
    added, removed = sorted(new_parts.keys() - old_parts.keys()), sorted(old_parts.keys() - new_parts.keys())
    return json_value({"schema": SCHEMA, "old": old.get("project"), "new": new.get("project"),
                       "changed": bool(added or removed or changed),
                       "parts": {"added": added, "removed": removed, "changed": changed}})


def _compare(old: dict, new: dict) -> dict:
    entry: dict = {}
    delta = new["volume_mm3"] - old["volume_mm3"]
    if abs(delta) > VOLUME_EPS_MM3:
        entry["volume_mm3"] = {"old": old["volume_mm3"], "new": new["volume_mm3"], "delta": delta,
                               "delta_pct": 100 * delta / old["volume_mm3"] if old["volume_mm3"] else None}
    for frame in ("model", "use", "print"):
        before, after = old["bbox"][frame], new["bbox"][frame]
        if any(abs(a - b) > SIZE_EPS_MM for a, b in zip(before["size_mm"], after["size_mm"])):
            entry[f"bbox_{frame}_size_mm"] = {"old": before["size_mm"], "new": after["size_mm"]}
    for key in ("qty", "printed", "solids"):
        if old.get(key) != new.get(key):
            entry[key] = {"old": old.get(key), "new": new.get(key)}
    return entry


def to_text(document: dict) -> str:
    """Compact human rendering of a diff document."""
    parts = document["parts"]
    if not document["changed"]:
        return "no differences"
    lines = [f"+ {name}" for name in parts["added"]] + [f"- {name}" for name in parts["removed"]]
    for item in parts["changed"]:
        details = []
        if "volume_mm3" in item:
            v = item["volume_mm3"]
            details.append(f"volume {v['old']:.1f} -> {v['new']:.1f} mm3 ({v['delta']:+.1f})")
        for key, value in item.items():
            if key.startswith("bbox_"):
                old, new = (" x ".join(f"{n:.1f}" for n in value[k]) for k in ("old", "new"))
                details.append(f"{key[5:-8]} size {old} -> {new} mm")
            elif key in ("qty", "printed", "solids"):
                details.append(f"{key} {value['old']} -> {value['new']}")
        lines.append(f"~ {item['name']}: " + "; ".join(details))
    return "\n".join(lines)
