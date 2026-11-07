"""Build outputs, manifest contents and atomicity."""
import json
from pathlib import Path

import cadquery as cq
import pytest

from agentcad.build import build, existing_build, sha256
from agentcad.geometry import volume
from agentcad.project import BuildError, ConfigError, Project

CUBE = '''
import cadquery as cq
from agentcad import Part, Pose

def parts():
    return [Part("cube", cq.Workplane("XY").box(10, 20, 30), use=Pose(x=100), print=Pose(rx=90), material="PETG",
                 color="#ff0000", qty=3),
            Part("bolt", cq.Workplane("XY").circle(2).extrude(10), printed=False)]
'''


def tree(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def test_files_and_manifest_fields(make_project):
    folder = make_project(CUBE)
    result = build(folder)
    for name in ("manifest.json", "assembly.step", "step/cube.step", "stl/cube.stl", "assembly/cube.stl"):
        assert (folder / "build" / name).is_file()
    manifest = json.loads((folder / "build" / "manifest.json").read_text())
    assert manifest["schema"] == "agentcad.manifest/1" and manifest["project"] == "t"
    assert {"agentcad_version", "created", "export", "printer", "parts", "sources"} <= set(manifest)
    cube = next(p for p in manifest["parts"] if p["name"] == "cube")
    assert (cube["qty"], cube["printed"], cube["material"], cube["color"]) == (3, True, "PETG", "#ff0000")
    assert cube["valid"] and cube["solids"] == 1 and cube["triangles"] == 12
    assert cube["area_mm2"] == pytest.approx(2200)
    assert cube["center_of_mass_mm"] == pytest.approx([100, 0, 0])
    assert cube["bbox"]["use"]["min_mm"][0] == pytest.approx(95)
    assert cube["bbox"]["print"]["min_mm"][2] == pytest.approx(0, abs=1e-9)
    assert cube["bbox"]["print"]["size_mm"] == pytest.approx([10, 30, 20])
    assert result.manifest == manifest and set(result.meshes) == {"cube", "bolt"}


def test_manifest_volume_is_measured_from_the_exported_step(make_project):
    folder = make_project(CUBE)
    build(folder)
    manifest = json.loads((folder / "build" / "manifest.json").read_text())
    for part in manifest["parts"]:
        step = cq.importers.importStep(str(folder / "build" / "step" / f"{part['name']}.step")).val()
        assert part["volume_mm3"] == pytest.approx(volume(step), rel=1e-6)
    assert manifest["parts"][0]["volume_mm3"] == pytest.approx(6000, rel=1e-6)


def test_hashes_match_the_files(make_project):
    folder = make_project(CUBE)
    build(folder)
    manifest = json.loads((folder / "build" / "manifest.json").read_text())
    for part in manifest["parts"]:
        for relative, digest in part["sha256"].items():
            assert sha256(folder / "build" / relative) == digest
    assert sha256(folder / "build" / "assembly.step") == manifest["files"]["assembly.step"]


def test_assembly_step_has_one_solid_per_part(make_project):
    folder = make_project(CUBE)
    build(folder)
    shape = cq.importers.importStep(str(folder / "build" / "assembly.step"))
    assert len(shape.vals()[0].Solids()) == 2


def test_out_directory(make_project, tmp_path):
    folder = make_project(CUBE)
    result = build(folder, tmp_path / "elsewhere")
    assert (tmp_path / "elsewhere" / "manifest.json").is_file() and not (folder / "build").exists()
    assert result.path == (tmp_path / "elsewhere").resolve()


def test_failing_model_leaves_the_previous_build_untouched(make_project):
    folder = make_project(CUBE)
    build(folder)
    before = tree(folder / "build")
    (folder / "model.py").write_text("def parts():\n    raise RuntimeError('boom')\n")
    with pytest.raises(BuildError, match="boom"):
        build(folder)
    assert tree(folder / "build") == before


def test_failing_export_leaves_the_previous_build_untouched(make_project, monkeypatch):
    folder = make_project(CUBE)
    build(folder)
    before = tree(folder / "build")
    real, calls = cq.exporters.export, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 5:
            raise OSError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(cq.exporters, "export", flaky)
    with pytest.raises(BuildError, match="disk full"):
        build(folder)
    assert tree(folder / "build") == before
    assert not list((folder / "build").glob(".tmp-*"))


def test_other_build_folder_content_survives_a_rebuild(make_project):
    folder = make_project(CUBE)
    build(folder)
    (folder / "build" / "renders").mkdir()
    (folder / "build" / "renders" / "sheet.png").write_bytes(b"png")
    build(folder)
    assert (folder / "build" / "renders" / "sheet.png").read_bytes() == b"png"


def test_existing_build_detects_staleness(make_project):
    folder = make_project(CUBE)
    build(folder)
    project = Project.load(folder)
    assert set(existing_build(project).meshes) == {"cube", "bolt"}
    (folder / "model.py").write_text(CUBE.replace("10, 20, 30", "10, 20, 31"))
    with pytest.raises(ConfigError, match="No fresh build"):
        existing_build(Project.load(folder))


def test_existing_build_detects_tampered_files(make_project):
    folder = make_project(CUBE)
    build(folder)
    with (folder / "build" / "stl" / "cube.stl").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ConfigError, match="exported files changed"):
        existing_build(Project.load(folder))


def test_existing_build_without_a_build(make_project):
    with pytest.raises(ConfigError, match="No fresh build"):
        existing_build(Project.load(make_project(CUBE)))
