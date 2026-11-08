"""Every built-in gate passes on good input and FAILs (or WARNs) on a fixture built to trip it."""
import cadquery as cq
import pytest
import trimesh
from helpers import by_id, check_project

from agentcad import checks as gates
from agentcad.checks import BUILTIN_IDS
from agentcad.report import Status

BOX = "cq.Workplane('XY').box({}, {}, {}, centered=(True, True, False))"


def one_part(shape: str, extra: str = "") -> str:
    return f"import cadquery as cq\nfrom agentcad import Part, Pose\n\ndef parts():\n    return [Part('p', {shape}{extra})]\n"


# -- examples ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["box_with_lid", "bracket"])
def test_good_examples_pass(copy_example, name):
    report = check_project(copy_example(name))
    assert report.exit_code() == 0, report.to_table()
    assert not [c for c in report.checks if c.status in (Status.FAIL, Status.ERROR, Status.WARN)]


def test_box_with_lid_ids_and_overhang_gate_on_both_parts(copy_example):
    report = check_project(copy_example("box_with_lid"))
    assert {c.part for c in by_id(report, "overhang")} == {"box", "lid"}
    assert all(c.status is Status.PASS for c in by_id(report, "overhang"))
    play = by_id(report, "lid_play_x")[0]
    assert 0.23 < play.measured["value_mm"] < 0.27 and play.limit == {"min_mm": 0.15, "max_mm": 0.4}


def test_bracket_probe_clearance_is_measured(copy_example):
    report = check_project(copy_example("bracket"))
    clearance = by_id(report, "m4_clearance_1")[0]
    assert clearance.measured["value_mm"] == pytest.approx(0.2, abs=0.01)
    assert by_id(report, "m4_no_overlap_1")[0].measured["value_mm3"] == 0


# -- broken fixture ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("id, part", [("interference", "left+right"), ("overhang", "tee"), ("bed_fit", "long_bar"),
                                      ("min_wall", "thin_cup"), ("left_right_gap", "left+right")])
def test_broken_project_fails_each_gate(broken_report, id, part):
    matches = by_id(broken_report, id, part)
    assert matches and matches[0].status is Status.FAIL, broken_report.to_table()


def test_broken_failures_are_located_and_numeric(broken_report):
    overhang = by_id(broken_report, "overhang", "tee")[0]
    assert overhang.measured["area_mm2"] == pytest.approx(300, abs=0.5)
    assert overhang.where[0]["z_mm"] == pytest.approx([25, 25]) and len(overhang.where[0]["centroid_mm"]) == 3
    assert by_id(broken_report, "interference", "left+right")[0].measured["overlap_mm3"] == pytest.approx(4000)
    assert by_id(broken_report, "interference", "left+right")[0].where[0]["centroid_mm"][0] == pytest.approx(5, abs=0.1)
    assert by_id(broken_report, "min_wall", "thin_cup")[0].measured["p1_mm"] < 0.7
    assert by_id(broken_report, "bed_fit", "long_bar")[0].measured["size_mm"][0] == pytest.approx(300)


def test_raising_check_becomes_error_and_the_run_continues(broken_report):
    boom = by_id(broken_report, "check_boom")[0]
    assert boom.status is Status.ERROR and "deliberate failure" in boom.summary
    assert by_id(broken_report, "tee_head_area")[0].status is Status.PASS
    assert broken_report.exit_code() == 1  # FAIL outranks ERROR


def test_broken_passes_untouched_gates(broken_report):
    assert by_id(broken_report, "valid", "tee")[0].status is Status.PASS
    assert by_id(broken_report, "watertight", "left")[0].status is Status.PASS
    assert by_id(broken_report, "interference", "left+tee")[0].status is Status.PASS


# -- gates that need their own fixtures ------------------------------------------------------------------------

def test_valid_gate_fails_on_a_non_solid():
    check = gates._valid("face", cq.Face.makePlane(10, 10))
    assert check.status is Status.FAIL and check.measured["solids"] == 0


def test_valid_gate_in_a_project(make_project):
    report = check_project(make_project(one_part("cq.Face.makePlane(10, 10)")), only=["valid"])
    assert report.checks[0].status is Status.FAIL


def test_single_solid_warns_on_two_islands(make_project):
    shape = "cq.Workplane('XY').pushPoints([(0, 0), (30, 0)]).box(10, 10, 10)"
    report = check_project(make_project(one_part(shape)), only=["single_solid"])
    assert report.checks[0].status is Status.WARN and report.checks[0].measured["solids"] == 2
    assert report.exit_code() == 0


def test_watertight_gate_fails_on_an_open_mesh():
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.update_faces(list(range(len(mesh.faces) - 1)))
    assert gates._watertight("p", mesh).status is Status.FAIL
    assert gates._watertight("p", trimesh.creation.box((10, 10, 10))).status is Status.PASS


def test_bed_contact_warns_on_a_sphere(make_project):
    report = check_project(make_project(one_part("cq.Workplane('XY').sphere(10)")), only=["bed_contact"])
    assert report.checks[0].status is Status.WARN and report.exit_code() == 0


def test_bed_fit_accepts_a_quarter_turn(make_project):
    toml = "[printer]\nbed = [250, 210, 250]\n"
    report = check_project(make_project(one_part(BOX.format(200, 240, 10)), toml), only=["bed_fit"])
    assert report.checks[0].status is Status.PASS and "rotate 90" in report.checks[0].summary


def test_min_wall_passes_thick_parts_and_skips_when_disabled(make_project):
    thick = one_part(BOX.format(20, 20, 20))
    enabled = check_project(make_project(thick, "[checks]\nmin_wall_mm = 1.2\n"), only=["min_wall"])
    assert enabled.checks[0].status is Status.PASS
    assert check_project(make_project(thick), only=["min_wall"]).checks[0].status is Status.SKIP


def test_allow_overlap_lists_a_pair_as_ok(make_project):
    model = ("import cadquery as cq\nfrom agentcad import Part, Pose\n\ndef parts():\n"
             "    b = cq.Workplane('XY').box(10, 10, 10)\n    return [Part('a', b), Part('b', b, use=Pose(x=5))]\n")
    assert check_project(make_project(model), only=["interference"]).exit_code() == 1
    report = check_project(make_project(model, "[checks]\nallow_overlap = [['b', 'a']]\n"), only=["interference"])
    assert report.checks[0].status is Status.PASS and "allow_overlap" in report.checks[0].summary


def test_overhang_allowance_is_configurable(make_project):
    model = (
        "import cadquery as cq\nfrom agentcad import Part\n\ndef parts():\n"
        "    stem = cq.Workplane('XY').box(10, 10, 25, centered=(True, True, False))\n"
        "    head = cq.Workplane('XY').workplane(offset=25).box(20, 20, 4, centered=(True, True, False))\n"
        "    return [Part('tee', stem.union(head))]\n")
    assert check_project(make_project(model), only=["overhang"]).exit_code() == 1
    assert check_project(make_project(model, "[checks]\noverhang_allow_mm2 = 400\n"), only=["overhang"]).exit_code() == 0
    assert check_project(make_project(model, "[checks]\nmax_overhang_deg = 90\n"), only=["overhang"]).exit_code() == 0


def test_unprinted_parts_skip_print_gates(make_project):
    model = ("import cadquery as cq\nfrom agentcad import Part\n\ndef parts():\n"
             "    return [Part('screw', cq.Workplane('XY').box(500, 5, 5), printed=False)]\n")
    report = check_project(make_project(model), only=["bed_fit", "overhang", "valid"])
    assert {c.id for c in report.checks} == {"valid"}


# -- context ---------------------------------------------------------------------------------------------------

def test_expect_records_pass_and_fail_with_units(make_project):
    model = one_part(BOX.format(10, 10, 10)) + (
        "\ndef checks(ctx):\n    ctx.expect('vol', 1000.0, min=900, max=1100, unit='mm3', part='p')\n"
        "    ctx.expect('small', 5.0, max=1.0, summary='custom words')\n    ctx.expect('low', 5.0, min=6)\n")
    report = check_project(make_project(model), only=["vol", "small", "low"])
    vol, small, low = report.checks
    assert vol.status is Status.PASS and vol.measured == {"value_mm3": 1000.0} and vol.limit == {"min_mm3": 900, "max_mm3": 1100}
    assert small.status is Status.FAIL and small.summary == "custom words"
    assert low.status is Status.FAIL and low.limit == {"min_mm": 6}


def test_expect_needs_a_bound_and_a_value(make_project):
    model = one_part(BOX.format(10, 10, 10)) + (
        "\ndef checks(ctx):\n    ctx.expect('a', 1.0)\n\ndef check_b(ctx):\n    ctx.expect('b', None, min=0)\n")
    report = check_project(make_project(model), only=["checks", "check_b"])
    assert [c.status for c in report.checks] == [Status.ERROR, Status.ERROR]
    assert "min, max or both" in report.checks[0].summary and "None" in report.checks[1].summary


def test_unknown_part_name_is_an_error_with_the_valid_names(make_project):
    model = one_part(BOX.format(10, 10, 10)) + "\ndef checks(ctx):\n    ctx.distance('p', 'q')\n"
    check = check_project(make_project(model), only=["checks"]).checks[0]
    assert check.status is Status.ERROR and "Unknown part 'q'" in check.summary and "p" in check.summary


def test_only_filters_builtin_and_project_ids(copy_example):
    folder = copy_example("box_with_lid")
    assert {c.id for c in check_project(folder, only=["overhang", "valid"]).checks} == {"overhang", "valid"}
    assert [c.id for c in check_project(folder, only=["lid_play_x"]).checks] == ["lid_play_x"]


def test_builtin_ids_are_documented_in_the_gate_table():
    assert set(BUILTIN_IDS) == {"valid", "single_solid", "watertight", "bed_fit", "bed_contact", "overhang",
                                "min_wall", "interference"}


def test_report_meta_names_the_tools_and_printer(broken_report):
    assert broken_report.meta["printer"] == "generic-220" and broken_report.meta["elapsed_s"] >= 0
    assert broken_report.meta["cadquery"] and broken_report.meta["agentcad"]


# -- fix hints ---------------------------------------------------------------------------------------------------

def hint_of(report, id, part=None):
    check = by_id(report, id, part)[0]
    assert check.status in (Status.FAIL, Status.WARN)
    return check.hint


def test_broken_failures_carry_a_hint_that_uses_the_measurements(broken_report):
    bed = hint_of(broken_report, "bed_fit", "long_bar")
    assert "84.0 mm too long in X" in bed and "generic-220 (220 x 220 x 250)" in bed and "Part.print" in bed
    assert "[printer]" in bed
    overhang = hint_of(broken_report, "overhang", "tee")
    assert "Part.print" in overhang and "where[0]" in overhang and "45 degree chamfer" in overhang
    assert "300 mm2 at z 25.0" in overhang
    overlap = hint_of(broken_report, "interference", "left+right")
    assert "Part.use" in overlap and "(5.0, 0.0, 10.0)" in overlap and "checks.allow_overlap" in overlap
    wall = hint_of(broken_report, "min_wall", "thin_cup")
    assert "1.2 mm" in wall and "0.59 mm" in wall
    # Loosening a limit is never the opening move.
    for text in (overhang, wall):
        assert ("raise checks" in text or "lower checks" in text) and not text.startswith(("raise", "lower"))
    assert all(len(h) < 260 for h in (bed, overhang, overlap, wall))


def test_passing_checks_never_carry_a_hint(copy_example, broken_report):
    report = check_project(copy_example("box_with_lid"))
    assert all(c.hint is None for c in report.checks)
    assert all(c.hint is None for c in broken_report.checks if c.status is Status.PASS)


def test_single_solid_warn_has_a_hint(make_project):
    shape = "cq.Workplane('XY').pushPoints([(0, 0), (30, 0)]).box(10, 10, 10)"
    report = check_project(make_project(one_part(shape)), only=["single_solid"])
    assert "2 islands" in report.checks[0].hint and "Part" in report.checks[0].hint


def test_bed_fit_hint_offers_the_diagonal_only_when_it_fits(make_project):
    toml = "[printer]\nbed = [220, 220, 250]\n"
    near = check_project(make_project(one_part(BOX.format(226, 10, 5)), toml), only=["bed_fit"]).checks[0]
    assert near.status is Status.FAIL and "rz=45" in near.hint and "10.0 mm too long in X" in near.hint
    tall = check_project(make_project(one_part(BOX.format(10, 10, 300)), toml), only=["bed_fit"]).checks[0]
    assert "50.0 mm too tall in Z" in tall.hint and "rx=90" in tall.hint and "rz=45" not in tall.hint


def test_valid_watertight_and_bed_contact_hints():
    assert "0 solid" in gates._valid("face", cq.Face.makePlane(10, 10)).hint
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.update_faces(list(range(len(mesh.faces) - 1)))
    assert "zero-thickness" in gates._watertight("p", mesh).hint
    assert gates._watertight("p", trimesh.creation.box((10, 10, 10))).hint is None


def test_bed_contact_warn_hint(make_project):
    check = check_project(make_project(one_part("cq.Workplane('XY').sphere(10)")), only=["bed_contact"]).checks[0]
    assert check.status is Status.WARN and "flat face" in check.hint
    assert check_project(make_project(one_part(BOX.format(20, 20, 20))), only=["bed_contact"]).checks[0].hint is None


def test_expect_hint_is_kept_on_fail_and_dropped_on_pass(make_project):
    model = one_part(BOX.format(10, 10, 10)) + (
        "\ndef checks(ctx):\n    ctx.expect('bad', 5.0, min=6, hint='make it bigger')\n"
        "    ctx.expect('good', 7.0, min=6, hint='make it bigger')\n    ctx.expect('plain', 5.0, min=6)\n")
    bad, good, plain = check_project(make_project(model), only=["bad", "good", "plain"]).checks
    assert bad.status is Status.FAIL and bad.hint == "make it bigger"
    assert good.status is Status.PASS and good.hint is None
    assert plain.hint is None
