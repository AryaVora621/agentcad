"""Build pipeline: export every part, measure the exports and publish them atomically."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import cadquery as cq
import trimesh

from . import __version__
from .flat import export_profile
from .geometry import area, bbox, center_of_mass, is_valid, solids_count, volume
from .project import BuildError, BuildResult, ConfigError, Project
from .report import json_value

MANIFEST_SCHEMA = "agentcad.manifest/1"
# Entries a build owns; anything else in the folder (renders, report.json) is left alone.
_OWNED = ("step", "stl", "assembly", "dxf", "svg", "assembly.step", "manifest.json")


def sha256(path: Path) -> str:
    """Hex SHA-256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes(project: Project) -> dict[str, str]:
    """Hash the config and every project .py file, so ``--no-build`` can tell a build is stale."""
    ignored = {"build", ".venv", "__pycache__"}
    paths = [project.config_path, *sorted(project.path.rglob("*.py"))]
    return {p.relative_to(project.path).as_posix(): sha256(p) for p in paths
            if not ignored & set(p.relative_to(project.path).parts)}


def build(project: Project | str | Path = ".", out: str | Path | None = None) -> BuildResult:
    """Export step/, stl/, assembly/ and manifest.json. The old build stays intact if anything fails."""
    project = project if isinstance(project, Project) else Project.load(project)
    target = Path(out).resolve() if out else project.path / "build"
    target.mkdir(parents=True, exist_ok=True)
    staging = target / f".tmp-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        meshes, records = _export_parts(project, staging)
        assembly = cq.Assembly(name="assembly")
        for part in project.parts.values():
            assembly.add(part.solid("use"), name=part.name)
        assembly.export(str(staging / "assembly.step"))
        manifest = {"schema": MANIFEST_SCHEMA, "project": project.name, "agentcad_version": __version__,
                    "created": datetime.now(UTC).isoformat(timespec="seconds"),
                    "export": project.export, "printer": asdict(project.printer), "parts": records,
                    "files": {"assembly.step": sha256(staging / "assembly.step")},
                    "sources": source_hashes(project)}
        (staging / "manifest.json").write_text(json.dumps(json_value(manifest), indent=2) + "\n")
        for name in _OWNED:
            _discard(target / name)
            # dxf/ and svg/ exist only while the project has cut parts; a stale folder is removed above.
            if (staging / name).exists():
                os.replace(staging / name, target / name)
    except BuildError:
        raise
    except Exception as exc:
        raise BuildError(f"Build failed: {type(exc).__name__}: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return BuildResult(project, target, json.loads((target / "manifest.json").read_text()), meshes)


def _discard(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _export_parts(project: Project, staging: Path) -> tuple[dict, list[dict]]:
    for folder in ("step", "stl", "assembly"):
        (staging / folder).mkdir()
    meshes, records = {}, []
    for part in project.parts.values():
        frames = {frame: part.solid(frame) for frame in ("model", "use", "print")}
        files = {}
        for folder, extension, frame in (("step", "step", "model"), ("stl", "stl", "print"),
                                         ("assembly", "stl", "use")):
            relative = f"{folder}/{part.name}.{extension}"
            cq.exporters.export(frames[frame], str(staging / relative), tolerance=project.export["tolerance"],
                                angularTolerance=project.export["angular_tolerance"])
            files[relative] = sha256(staging / relative)
        flat = _export_flat(project, part, frames["print"], staging, files) if part.cut else None
        mesh = trimesh.load(str(staging / "stl" / f"{part.name}.stl"), force="mesh")
        meshes[part.name] = mesh
        model = frames["model"]
        # A cut part is a plate for a laser or router, so it never counts as printed in the manifest.
        records.append({"name": part.name, "qty": part.qty, "printed": part.printed and part.cut is None,
                        "material": part.material,
                        "color": part.color, "volume_mm3": volume(model), "area_mm2": area(model),
                        "bbox": {frame: bbox(shape) for frame, shape in frames.items()},
                        "center_of_mass_mm": center_of_mass(frames["use"]), "solids": solids_count(model),
                        "valid": is_valid(model), "triangles": len(mesh.faces), "sha256": files,
                        **({"cut": part.cut, "flat": flat} if flat else {})})
    return meshes, records


def _export_flat(project: Project, part, solid: cq.Shape, staging: Path, files: dict) -> dict:
    """Write the part's DXF and SVG profile, hash them into ``files`` and return its manifest record."""
    for folder in ("dxf", "svg"):
        (staging / folder).mkdir(exist_ok=True)
    dxf, svg = f"dxf/{part.name}.dxf", f"svg/{part.name}.svg"
    try:
        record = export_profile(solid, project.flat, staging / dxf, staging / svg)
    except ValueError as exc:
        raise BuildError(f"Part {part.name}: {exc}") from exc
    hashes = {dxf: sha256(staging / dxf), svg: sha256(staging / svg)}
    files.update(hashes)
    return {**record, "files": hashes}


def existing_build(project: Project) -> BuildResult:
    """Load ``build/`` for ``--no-build``; refuse a build whose sources or files changed since it was made."""
    path = project.path / "build"
    try:
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise ValueError("unknown manifest schema")
        if manifest.get("sources") != source_hashes(project):
            raise ValueError("model or config changed since the build")
        files = dict(manifest.get("files", {}))
        for record in manifest["parts"]:
            files.update(record["sha256"])
        if any(sha256(path / name) != digest for name, digest in files.items()):
            raise ValueError("exported files changed since the build")
        meshes = {name: trimesh.load(str(path / "stl" / f"{name}.stl"), force="mesh") for name in project.parts}
    except (OSError, ValueError, KeyError) as exc:
        raise ConfigError(f"No fresh build in {path}: {exc}. Run 'agentcad build' or drop --no-build.") from exc
    return BuildResult(project, path, manifest, meshes)
