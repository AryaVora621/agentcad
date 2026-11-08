"""Flat (laser, CNC, waterjet) parts: profile gates, DXF and SVG export, config and printing-gate exclusion."""
import json
import re
import time

import ezdxf
import pytest
from ezdxf import bbox as dxf_bbox
from helpers import by_id, check_project

from agentcad.build import build
from agentcad.flat import FLAT_IDS, GATE_DOCS
from agentcad.project import ConfigError, Project

HEADER = '''
import cadquery as cq
from agentcad import Part

def plate(w=60, h=40, t=3):
    return cq.Workplane("XY").box(w, h, t, centered=(True, True, False))
'''


def model(body: str, cut: str = "laser", extra: str = "") -> str:
    return HEADER + f'''
def parts():
    shape = {body}
    return [Part("plate", shape, cut={cut!r}{extra})]
'''


def only(report, id: str):
    (check,) = by_id(report, id, "plate")
    return check


def test_every_flat_gate_has_a_doc_line():
    assert FLAT_IDS == ("flat_profile", "sheet_fit", "min_feature", "inner_corners", "hole_size")
    assert set(GATE_DOCS) == set(FLAT_IDS) and all(GATE_DOCS.values())


def test_plain_plate_passes_every_flat_gate(make_project):
    report = check_project(make_project(model("plate()")))
    assert [c.status.value for c in report.checks if c.id in FLAT_IDS] == ["PASS"] * 4
    profile = only(report, "flat_profile")
    assert profile.measured["profile_area_mm2"] == pytest.approx(2400)
    assert profile.measured["thickness_mm"] == pytest.approx(3)
    assert profile.measured["volume_error"] < 1e-9


def test_a_half_millimetre_web_fails_min_feature_near_the_web(make_project):
    # Two 20 x 20 windows 0.5 mm apart leave a 0.5 mm web at x = 0 between them.
    body = '''plate(80, 50).cut(cq.Workplane("XY").box(20, 20, 10).translate((-10.25, 0, 0))).cut(
        cq.Workplane("XY").box(20, 20, 10).translate((10.25, 0, 0)))'''
    check = only(check_project(make_project(model(body))), "min_feature")
    assert check.status.value == "FAIL" and check.hint
    webs = [w for w in check.where if w["kind"] == "web"]
    assert webs and len(check.where) <= 10
    cx, cy = webs[0]["centre_mm"]
    # Sheet coordinates put the outline minimum at 0, 0, so the web is at the plate centre (40, 25).
    assert abs(cx - 40) < 1 and abs(cy - 25) < 11
    assert webs[0]["size_mm"][0] == pytest.approx(0.5, abs=0.1)


def test_a_thin_tab_fails_but_a_wide_one_passes(make_project):
    tab = 'plate().union(cq.Workplane("XY").box(0.4, 8, 3, centered=(True, False, False)).translate((0, 20, 0)))'
    wide = 'plate().union(cq.Workplane("XY").box(4, 8, 3, centered=(True, False, False)).translate((0, 20, 0)))'
    assert only(check_project(make_project(model(tab))), "min_feature").status.value == "FAIL"
    assert only(check_project(make_project(model(wide))), "min_feature").status.value == "PASS"


def test_a_slot_narrower_than_the_kerf_fails(make_project):
    body = 'plate().cut(cq.Workplane("XY").box(30, 0.1, 10))'
    check = only(check_project(make_project(model(body), "[flat]\nkerf_mm = 0.15\n")), "min_feature")
    assert check.status.value == "FAIL"
    assert check.where[0]["kind"] == "slot" and check.where[0]["area_mm2"] == pytest.approx(3.0, rel=0.1)
    # The same slot passes once the kerf is thinner than it.
    thin_kerf = only(check_project(make_project(model(body), "[flat]\nkerf_mm = 0.05\n")), "min_feature")
    assert thin_kerf.status.value == "PASS"


def test_a_sharp_inside_corner_warns_for_cnc_until_it_has_a_radius(make_project):
    sharp = 'plate(80, 60).cut(cq.Workplane("XY").box(30, 20, 10).translate((10, 0, 0)))'
    round_ = ('plate(80, 60).cut(cq.Workplane("XY").box(30, 20, 10).edges("|Z").fillet(2).translate((10, 0, 0)))')
    warn = only(check_project(make_project(model(sharp, "cnc"))), "inner_corners")
    assert warn.status.value == "WARN" and warn.hint and len(warn.where) == 4
    assert warn.limit["tool_diameter_mm"] == pytest.approx(3.175)
    assert only(check_project(make_project(model(round_, "cnc"))), "inner_corners").status.value == "PASS"


def test_inner_corners_only_run_for_cnc_parts(make_project):
    sharp = 'plate(80, 60).cut(cq.Workplane("XY").box(30, 20, 10))'
    assert by_id(check_project(make_project(model(sharp, "laser"))), "inner_corners") == []
    assert by_id(check_project(make_project(model(sharp, "waterjet"))), "inner_corners") == []


def test_a_stepped_plate_is_not_a_through_cut(make_project):
    body = 'plate(60, 40, 6).cut(cq.Workplane("XY").box(30, 40, 3).translate((15, 0, 4.5)))'
    check = only(check_project(make_project(model(body))), "flat_profile")
    assert check.status.value == "FAIL" and "straight" in check.hint
    assert check.measured["volume_error"] > 0.01
    assert check.where and check.where[0]["centre_mm"][0] > 30


def test_a_chamfered_plate_fails_on_the_sections_alone(make_project):
    # A small chamfer keeps the volume within 1 percent but the 10 and 90 percent sections differ.
    body = 'plate(200, 200, 3).faces(">Z").edges().chamfer(0.5)'
    check = only(check_project(make_project(model(body))), "flat_profile")
    assert check.status.value == "FAIL" and check.measured["volume_error"] < 0.01
    assert check.measured["mismatch_90pct_mm2"] > 100


def test_sheet_fit_by_size_and_by_rotation(make_project):
    toml = "[flat]\nsheet = [600, 400]\n"
    huge = only(check_project(make_project(model("plate(700, 100)"), toml)), "sheet_fit")
    assert huge.status.value == "FAIL" and huge.hint and huge.limit["sheet_mm"] == [600, 400]
    tall = only(check_project(make_project(model("plate(300, 500)"), toml)), "sheet_fit")
    assert tall.status.value == "PASS" and tall.measured["rotated"] is True and "turn" in tall.summary
    plain = only(check_project(make_project(model("plate(500, 300)"), toml)), "sheet_fit")
    assert plain.measured["rotated"] is False


def test_small_circular_holes_warn_and_a_zero_ratio_disables_the_gate(make_project):
    body = 'plate(60, 40, 5).faces(">Z").workplane().pushPoints([(-10, 0), (10, 0)]).hole(2.0)'
    warn = only(check_project(make_project(model(body))), "hole_size")
    assert warn.status.value == "WARN" and len(warn.where) == 2
    assert warn.where[0]["diameter_mm"] == pytest.approx(2.0, abs=0.01)
    assert warn.limit["min_diameter_mm"] == pytest.approx(5)
    off = only(check_project(make_project(model(body), "[flat]\nmin_hole_ratio = 0\n")), "hole_size")
    assert off.status.value == "SKIP"


def test_hole_size_ignores_slots_and_squares(make_project):
    body = 'plate(60, 40, 5).cut(cq.Workplane("XY").slot2D(12, 2).extrude(10, both=True))'
    assert only(check_project(make_project(model(body))), "hole_size").status.value == "PASS"


def test_printing_gates_skip_cut_parts_but_not_printed_ones(make_project):
    source = HEADER + '''
def parts():
    return [Part("plate", plate(), cut="laser"), Part("block", cq.Workplane("XY").box(10, 10, 10))]
'''
    report = check_project(make_project(source))
    for gate in ("watertight", "bed_fit", "bed_contact", "overhang"):
        assert [c.part for c in by_id(report, gate)] == ["block"], gate
    assert [c.part for c in by_id(report, "flat_profile")] == ["plate"]


def test_only_selects_flat_gates(make_project):
    report = check_project(make_project(model("plate()")), only=["sheet_fit"])
    assert [c.id for c in report.checks] == ["sheet_fit"]


def test_dxf_is_millimetres_with_closed_loops_and_the_measured_outline(make_project):
    body = 'plate(60, 40).faces(">Z").workplane().pushPoints([(-20, 0), (20, 0)]).hole(4)'
    folder = make_project(model(body))
    result = build(folder)
    document = ezdxf.readfile(str(folder / "build" / "dxf" / "plate.dxf"))
    assert document.header["$INSUNITS"] == 4
    loops = [e for e in document.modelspace() if e.dxftype() == "CIRCLE"
             or (e.dxftype() == "LWPOLYLINE" and e.closed)]
    assert len(loops) == 3  # two holes plus the outline
    extents = dxf_bbox.extents(document.modelspace())
    assert list(extents.size)[:2] == pytest.approx([60, 40], abs=1e-6)
    assert list(extents.extmin)[:2] == pytest.approx([0, 0], abs=1e-6)
    flat = next(p for p in result.manifest["parts"] if p["name"] == "plate")["flat"]
    assert flat["outline_mm"] == pytest.approx([60, 40]) and flat["holes"] == 2
    assert flat["thickness_mm"] == pytest.approx(3) and flat["profile_area_mm2"] == pytest.approx(2400 - 2 * 3.14159 * 4)
    assert flat["cut_length_mm"] == pytest.approx(200 + 2 * 3.14159 * 4, rel=1e-3)
    assert flat["kerf_compensated"] is False
    assert set(flat["files"]) == {"dxf/plate.dxf", "svg/plate.svg"}


def test_svg_is_in_millimetres_and_hashes_are_in_the_manifest(make_project):
    folder = make_project(model('plate(60, 40).edges("|Z").fillet(5)'))
    build(folder)
    text = (folder / "build" / "svg" / "plate.svg").read_text()
    assert re.search(r'<svg [^>]*width="60.0000mm" height="40.0000mm" viewBox="0 0 60.0000 40.0000"', text)
    assert text.count("<path") == 1
    manifest = json.loads((folder / "build" / "manifest.json").read_text())
    record = manifest["parts"][0]
    assert set(record["flat"]["files"]) <= set(record["sha256"])
    assert record["cut"] == "laser"
    assert record["printed"] is False  # a laser plate is not a print job


def test_kerf_compensation_grows_the_outline_by_half_the_kerf_per_side(make_project):
    body = 'plate(60, 40).faces(">Z").workplane().hole(10)'
    nominal = make_project(model(body), "[flat]\nkerf_mm = 0.2\n")
    compensated = make_project(model(body), "[flat]\nkerf_mm = 0.2\ncompensate_kerf = true\n")
    sizes, radii = [], []
    for folder in (nominal, compensated):
        build(folder)
        document = ezdxf.readfile(str(folder / "build" / "dxf" / "plate.dxf"))
        sizes.append(list(dxf_bbox.extents(document.modelspace()).size)[:2])
        radii.append(next(e.dxf.radius for e in document.modelspace() if e.dxftype() == "CIRCLE"))
    assert sizes[0] == pytest.approx([60, 40], abs=1e-6)
    assert sizes[1] == pytest.approx([60.2, 40.2], abs=1e-6)
    assert radii == pytest.approx([5, 4.9], abs=1e-6)
    manifest = json.loads((compensated / "build" / "manifest.json").read_text())
    flat = manifest["parts"][0]["flat"]
    assert flat["kerf_compensated"] is True and flat["kerf_mm"] == pytest.approx(0.2)
    assert flat["outline_mm"] == pytest.approx([60, 40])


def test_kerf_larger_than_a_hole_is_a_build_error(make_project):
    from agentcad.project import BuildError

    folder = make_project(model('plate().faces(">Z").workplane().hole(0.1)'), "[flat]\nkerf_mm = 0.3\n"
                          "compensate_kerf = true\n")
    with pytest.raises(BuildError, match="collapses a hole"):
        build(folder)


def test_a_rebuild_without_cut_parts_removes_stale_dxf_and_svg(make_project):
    folder = make_project(model("plate()"))
    build(folder)
    assert (folder / "build" / "dxf" / "plate.dxf").is_file()
    (folder / "model.py").write_text(HEADER + 'def parts():\n    return [Part("plate", plate())]\n')
    build(folder)
    assert not (folder / "build" / "dxf").exists() and not (folder / "build" / "svg").exists()


def test_no_build_accepts_a_fresh_flat_build(make_project):
    from agentcad.build import existing_build

    folder = make_project(model("plate()"))
    build(folder)
    assert existing_build(Project.load(folder)).manifest["parts"][0]["flat"]["holes"] == 0
    (folder / "build" / "dxf" / "plate.dxf").write_text("changed")
    with pytest.raises(ConfigError, match="changed"):
        existing_build(Project.load(folder))


def test_gates_stay_fast_on_a_large_plate_with_twenty_holes(make_project):
    points = [(x, y) for x in range(-120, 121, 60) for y in range(-60, 61, 40)][:20]
    body = (f'plate(300, 200).edges("|Z").fillet(8).faces(">Z").workplane().pushPoints({points}).hole(5)')
    folder = make_project(model(body, "cnc"))
    project = Project.load(folder)
    result = build(project)
    from agentcad.checks import run_checks

    started = time.perf_counter()
    report = run_checks(project, result, only=list(FLAT_IDS))
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0, elapsed
    assert {c.id for c in report.checks} == set(FLAT_IDS)
    assert report.exit_code() == 0 or all(c.status.value != "FAIL" for c in report.checks)


# Config and model errors reach the user as exit 2 with one line, not a traceback.

def test_unknown_flat_key_exits_2(make_project, cli):
    folder = make_project(model("plate()"), "[flat]\nkerf = 0.1\n")
    code, _, err = cli("check", folder)
    assert code == 2 and "flat.kerf" in err and "Traceback" not in err


@pytest.mark.parametrize("toml, message", [
    ("[flat]\nsheet = [600]\n", "flat.sheet"),
    ("[flat]\nsheet = [600, -1]\n", "flat.sheet"),
    ("[flat]\nkerf_mm = -0.1\n", "flat.kerf_mm"),
    ("[flat]\nmin_hole_ratio = \"x\"\n", "flat.min_hole_ratio"),
    ("[flat]\ncompensate_kerf = 1\n", "flat.compensate_kerf"),
])
def test_bad_flat_values_exit_2(make_project, cli, toml, message):
    code, _, err = cli("check", make_project(model("plate()"), toml))
    assert code == 2 and message in err


def test_a_bad_cut_value_exits_2_through_the_cli(make_project, cli):
    code, _, err = cli("check", make_project(model("plate()", cut="plasma")))
    assert code == 2 and "cut must be" in err and "plasma" in err and "Traceback" not in err


def test_a_bad_cut_value_with_json_has_the_error_document(make_project, cli):
    code, out, _ = cli("check", make_project(model("plate()", cut="plasma")), "--json")
    assert code == 2 and json.loads(out)["schema"] == "agentcad.error/1"
