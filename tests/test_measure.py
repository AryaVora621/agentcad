"""Interference, distance, section, slide and play against known answers."""
import cadquery as cq
import pytest

from agentcad import Pose, measure
from agentcad.build import build
from agentcad.checks import CheckContext
from agentcad.geometry import to_mesh
from agentcad.project import Project


def block(x: float, y: float, z: float, at=(0, 0, 0)) -> cq.Shape:
    return cq.Workplane("XY").box(x, y, z, centered=False).translate(at).val()


def test_overlap_volume():
    # 10 x 10 x 10 cubes offset by 9 in x share a 1 x 10 x 10 slab.
    assert measure.interference(block(10, 10, 10), block(10, 10, 10, (9, 0, 0))) == pytest.approx(100)


def test_disjoint_and_touching_have_no_interference():
    a = block(10, 10, 10)
    assert measure.interference(a, block(10, 10, 10, (15, 0, 0))) == 0
    assert measure.interference(a, block(10, 10, 10, (10, 0, 0))) == 0
    assert measure.distance(a, block(10, 10, 10, (10, 0, 0))) == pytest.approx(0)


def test_touching_faces_with_overlapping_footprint_are_not_interference():
    # Regression: OCC returns a degenerate shell with a bogus volume for coplanar contact.
    base = block(60, 40, 10)
    plate = block(60, 40, 2, (0, 0, 10))
    assert measure.interference(plate, base) == 0
    assert measure.interference(base, plate) == 0


def test_distance_between_separated_boxes():
    assert measure.distance(block(10, 10, 10), block(10, 10, 10, (15, 0, 0))) == pytest.approx(5.0)


def test_distance_is_zero_when_overlapping():
    assert measure.distance(block(10, 10, 10), block(10, 10, 10, (5, 0, 0))) == 0


def test_mesh_interference_uses_manifold():
    a, b = block(10, 10, 10), block(10, 10, 10, (9, 0, 0))
    assert measure.interference(to_mesh(a), to_mesh(b)) == pytest.approx(100, rel=1e-3)
    assert measure.interference(a, to_mesh(b)) == pytest.approx(100, rel=1e-3)


def test_section_of_a_box_at_mid_height():
    cut = measure.section(block(10, 20, 30), "z", 15)
    assert cut["area_mm2"] == pytest.approx(200)
    assert cut["bbox_mm"]["size_mm"][:2] == pytest.approx([10, 20])
    assert cut["loops"] == 1


def test_section_subtracts_holes():
    plate = cq.Workplane("XY").box(20, 20, 10).faces(">Z").workplane().hole(6).val()
    cut = measure.section(plate, "z", 0)
    assert cut["area_mm2"] == pytest.approx(400 - 3.14159265 * 9, abs=0.05)
    assert cut["loops"] == 2


def test_section_outside_the_part_is_empty_and_bad_axis_raises():
    assert measure.section(block(10, 10, 10), "z", 50)["area_mm2"] == 0
    with pytest.raises(ValueError, match="axis"):
        measure.section(block(10, 10, 10), "w", 0)


def test_unit_direction_normalises_and_rejects_zero():
    assert list(measure.unit_direction((0, 0, 5))) == [0, 0, 1]
    with pytest.raises(ValueError):
        measure.unit_direction((0, 0, 0))


def test_slide_apart_is_zero_and_slide_through_overlaps():
    fixed, mover = block(10, 10, 10), block(10, 10, 10, (0, 0, 10))
    up = measure.slide(mover, fixed, (0, 0, 1), 20, steps=4)
    assert [s["overlap_mm3"] for s in up] == [0, 0, 0, 0, 0]
    down = measure.slide(mover, fixed, (0, 0, -1), 10, steps=2)
    assert down[-1]["overlap_mm3"] == pytest.approx(1000)


def test_slide_accepts_several_obstacles():
    a, b = block(10, 10, 10), block(10, 10, 10, (20, 0, 0))
    steps = measure.slide(block(10, 10, 10, (0, 0, 20)), [a, b], (0, 0, -1), 20, steps=1)
    assert steps[-1]["overlap_mm3"] == pytest.approx(1000)


def test_play_finds_the_gap_and_none_when_clear():
    fixed, mover = block(10, 10, 10), block(10, 10, 10, (12, 0, 0))
    assert measure.play(mover, fixed, (-1, 0, 0), max_mm=5) == pytest.approx(2.0, abs=0.02)
    assert measure.play(mover, fixed, (1, 0, 0), max_mm=5) is None
    assert measure.play(fixed, fixed, (1, 0, 0), max_mm=5) == 0.0


def test_lid_lifts_free_and_has_quarter_millimetre_play(copy_example):
    project = Project.load(copy_example("box_with_lid"))
    ctx = CheckContext(project, build(project))
    assert all(s["overlap_mm3"] == 0 for s in ctx.slide("lid", "box", (0, 0, 1), 20, steps=10))
    # Pushed down into the box the lid collides, so the zero above is a measurement, not a default.
    assert ctx.slide("lid", "box", (0, 0, -1), 10, steps=2)[-1]["overlap_mm3"] > 100
    for direction in ((1, 0, 0), (0, 1, 0), (-1, 0, 0)):
        assert ctx.play("lid", "box", direction, max_mm=2) == pytest.approx(0.25, abs=0.02)


def test_context_accepts_probe_solids_and_lists(copy_example):
    project = Project.load(copy_example("box_with_lid"))
    ctx = CheckContext(project, build(project))
    probe = cq.Workplane("XY").box(10, 10, 10, centered=False).translate((100, 0, 0))
    assert ctx.distance("box", probe) == pytest.approx(70, abs=0.01)
    assert ctx.interference("box", probe) == 0
    assert ctx.play(probe, ["box", "lid"], (-1, 0, 0), max_mm=100) == pytest.approx(70, abs=0.05)
    assert ctx.section(["box", "lid"], "z", 15)["area_mm2"] == pytest.approx(60 * 40 - 56 * 36)


def test_pose_helper_used_by_use_frame():
    moved = Pose(z=10).apply(block(10, 10, 10))
    assert measure.interference(moved, block(10, 10, 10)) == 0


# -- interference robustness (OCC common is order dependent for coplanar faces) -------------------------------------

def tight_lid_project(copy_example):
    folder = copy_example("box_with_lid")
    model = folder / "model.py"
    model.write_text(model.read_text().replace("CLEARANCE = 0.25", "CLEARANCE = -0.5"))
    return folder


def test_oversized_lip_fails_the_gate_in_both_orders(copy_example):
    from helpers import by_id, check_project

    folder = tight_lid_project(copy_example)
    report = check_project(folder)
    gate = by_id(report, "interference", "box+lid")[0]
    assert gate.status.value == "FAIL"
    assert gate.measured["overlap_mm3"] == pytest.approx(558, rel=0.01)
    assert gate.where[0]["centroid_mm"] is not None
    project = Project.load(folder)
    box, lid = project.parts["box"].solid("use"), project.parts["lid"].solid("use")
    assert measure.interference(box, lid) == pytest.approx(measure.interference(lid, box), rel=1e-6)
    assert measure.interference(box, lid) == pytest.approx(558, rel=0.01)


def test_box_resting_on_a_box_is_still_zero():
    low, high = block(60, 40, 10), block(60, 40, 5, (0, 0, 10))
    assert measure.interference(low, high) < 0.01
    assert measure.interference(high, low) < 0.01


def test_pin_filling_a_hole_is_finite():
    plate = cq.Workplane("XY").box(20, 20, 10).faces(">Z").workplane().hole(6).val()
    pin = cq.Workplane("XY").circle(3).extrude(10).translate((0, 0, -5)).val()
    value = measure.interference(plate, pin)
    assert 0 <= value < 1  # the chained comparison also rejects NaN


def test_mesh_fallback_catches_a_shell_only_common(monkeypatch):
    a, b = block(10, 10, 10), block(10, 10, 10, (9, 0, 0))
    shell_only = cq.Workplane("XY").box(1, 1, 1).faces(">Z").shell(-0.1).val().Shells()[0]
    monkeypatch.setattr(measure, "_brep_common", lambda x, y: cq.Compound.makeCompound([shell_only]))
    detail = measure.overlap(a, b)
    assert detail["volume_mm3"] == pytest.approx(100, rel=1e-2)
    assert detail["centroid_mm"][0] == pytest.approx(9.5, abs=0.1)
