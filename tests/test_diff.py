"""Manifest diffs."""
import json

import pytest

from agentcad.build import build
from agentcad.diff import diff_manifests, load_manifest, to_text

MODEL = '''
import cadquery as cq
from agentcad import Part

SIZE = {size}

def parts():
    out = [Part("cube", cq.Workplane("XY").box(SIZE, 10, 10))]
    {extra}
    return out
'''


def manifest(make_project, size=10, extra="pass"):
    folder = make_project(MODEL.format(size=size, extra=extra))
    build(folder)
    return folder / "build"


def test_identical_builds_have_no_diff(make_project):
    build_dir = manifest(make_project)
    document = diff_manifests(load_manifest(build_dir), load_manifest(build_dir / "manifest.json"))
    assert document["changed"] is False and to_text(document) == "no differences"


def test_changed_part_reports_volume_and_bbox(make_project):
    old, new = manifest(make_project, 10), manifest(make_project, 20)
    document = diff_manifests(load_manifest(old), load_manifest(new))
    (item,) = document["parts"]["changed"]
    assert item["name"] == "cube" and item["volume_mm3"]["delta"] == pytest.approx(1000)
    assert item["volume_mm3"]["delta_pct"] == pytest.approx(100)
    assert item["bbox_use_size_mm"] == {"old": [10, 10, 10], "new": [20, 10, 10]}
    assert "volume 1000.0 -> 2000.0 mm3 (+1000.0)" in to_text(document)


def test_added_and_removed_parts(make_project):
    extra = "out.append(Part('pin', cq.Workplane('XY').circle(2).extrude(5)))"
    old, new = manifest(make_project, extra=extra), manifest(make_project)
    forward = diff_manifests(load_manifest(old), load_manifest(new))
    assert forward["parts"]["removed"] == ["pin"] and forward["changed"]
    backward = diff_manifests(load_manifest(new), load_manifest(old))
    assert backward["parts"]["added"] == ["pin"] and "+ pin" in to_text(backward)


def test_load_manifest_rejects_other_files(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": "other"}))
    with pytest.raises(ValueError, match="not an agentcad.manifest/1"):
        load_manifest(tmp_path)
    with pytest.raises(ValueError, match="Cannot read"):
        load_manifest(tmp_path / "missing.json")
