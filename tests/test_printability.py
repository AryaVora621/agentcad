"""Overhang, bed fit and bed contact on shapes with known answers."""
import cadquery as cq
import numpy as np
import pytest
from helpers import print_mesh

from agentcad import printability as pr


def on_bed(wp: cq.Workplane) -> cq.Workplane:
    z = wp.val().BoundingBox().zmin
    return wp.translate((0, 0, -z))


def tee() -> cq.Workplane:
    stem = cq.Workplane("XY").box(10, 10, 25, centered=(True, True, False))
    head = cq.Workplane("XY").workplane(offset=25).box(20, 20, 4, centered=(True, True, False))
    return stem.union(head)


def funnel(angle_from_horizontal: float) -> cq.Workplane:
    """A 10 mm base that widens upward so its sloped sides face down at the given angle from horizontal."""
    rise = 10.0
    spread = rise / np.tan(np.radians(angle_from_horizontal))
    points = [(-5, 0), (5, 0), (5 + spread, rise), (-5 - spread, rise)]
    return cq.Workplane("XZ").polyline(points).close().extrude(10, both=True)


def test_cube_passes_and_bed_face_is_excluded():
    result = pr.overhang(print_mesh(on_bed(cq.Workplane("XY").box(20, 20, 20))))
    assert result["area_mm2"] == 0 and result["region_count"] == 0


def test_flat_ceiling_is_found_with_area_and_height():
    result = pr.overhang(print_mesh(tee()))
    assert result["area_mm2"] == pytest.approx(400 - 100, abs=0.01)
    assert result["region_count"] == 1
    region = result["regions"][0]
    assert region["z_mm"] == pytest.approx([25, 25])
    assert region["centroid_mm"][:2] == pytest.approx([0, 0], abs=0.01)


def test_chamfer_threshold_is_measured_from_the_normal():
    assert pr.overhang(print_mesh(funnel(50)), max_deg=45)["area_mm2"] == 0
    assert pr.overhang(print_mesh(funnel(30)), max_deg=45)["area_mm2"] > 100
    # The same 50 degree slope needs support once the limit is tightened to 30 degrees from vertical.
    assert pr.overhang(print_mesh(funnel(50)), max_deg=30)["area_mm2"] > 100


def test_overhang_rejects_a_bad_angle():
    with pytest.raises(ValueError):
        pr.overhang(print_mesh(tee()), max_deg=120)


def test_regions_are_separate_and_sorted_by_area():
    two = cq.Workplane("XY").box(10, 10, 2, centered=(True, True, False)).translate((0, 0, 10))
    two = two.union(cq.Workplane("XY").box(6, 6, 2, centered=(True, True, False)).translate((30, 0, 10)))
    two = two.union(cq.Workplane("XY").box(4, 4, 30, centered=(True, True, False)).translate((30, 0, 0)))
    two = two.union(cq.Workplane("XY").box(4, 4, 30, centered=(True, True, False)))
    result = pr.overhang(print_mesh(two))
    assert result["region_count"] == 2
    assert result["regions"][0]["area_mm2"] > result["regions"][1]["area_mm2"]


def test_bed_fit_passes_and_fails():
    wide = {"size_mm": [250, 100, 20]}
    assert pr.bed_fit(wide, (260, 260, 260))["fits"]
    big = pr.bed_fit({"size_mm": [300, 300, 20]}, (220, 220, 250))
    assert not big["fits"]


def test_bed_fit_reports_a_quarter_turn():
    fit = pr.bed_fit({"size_mm": [200, 240, 20]}, (250, 210, 250), margin=2.0)
    assert fit["fits"] and fit["rotation_z_deg"] == 90
    assert pr.bed_fit({"size_mm": [200, 100, 20]}, (250, 210, 250))["rotation_z_deg"] == 0


def test_bed_fit_checks_height_and_margin():
    assert not pr.bed_fit({"size_mm": [100, 100, 300]}, (220, 220, 250))["fits"]
    assert pr.bed_fit({"size_mm": [216, 100, 10]}, (220, 220, 250), margin=2)["fits"]
    assert not pr.bed_fit({"size_mm": [217, 100, 10]}, (220, 220, 250), margin=2)["fits"]


def test_bed_fit_takes_a_mesh():
    assert pr.bed_fit(print_mesh(tee()), (220, 220, 250))["size_mm"] == pytest.approx([20, 20, 29])


def test_bed_contact_areas():
    cube = print_mesh(on_bed(cq.Workplane("XY").box(20, 20, 20)))
    assert pr.bed_contact(cube) == pytest.approx(400)
    ball = print_mesh(on_bed(cq.Workplane("XY").sphere(10)), tolerance=0.05)
    assert pr.bed_contact(ball) < 5  # a sphere touches the bed at a point


# -- first layer exemption and the limit boundary -----------------------------------------------------------------

def chamfered_cube(length: float, bottom_angle_from_horizontal: float = 45.0, height: float | None = None):
    """20 mm cube with its bottom edges chamfered; ``height`` is the chamfer's vertical extent."""
    import math

    height = height if height is not None else length
    run = height / math.tan(math.radians(bottom_angle_from_horizontal))
    return cq.Workplane("XY").box(20, 20, 20, centered=(True, True, False)).edges("<Z").chamfer(height, run)


def test_small_bottom_chamfer_is_exempt_and_reported():
    mesh = print_mesh(chamfered_cube(0.4))
    flagged = pr.overhang(mesh, max_deg=44)
    assert flagged["area_mm2"] == 0 and flagged["first_layer_area_mm2"] > 5
    # At 45 the 45 degree chamfer is exactly at the limit, so nothing needs the exemption.
    assert pr.overhang(mesh, max_deg=45)["area_mm2"] == 0


def test_exemption_is_what_lets_the_chamfer_pass():
    result = pr.overhang(print_mesh(chamfered_cube(0.4)), max_deg=44, first_layer_mm=0)
    assert result["area_mm2"] > 5 and result["first_layer_area_mm2"] == 0


def test_tall_shallow_chamfer_still_fails():
    # 30 degrees from horizontal is 60 from vertical, and its centroid sits well above the first layer.
    result = pr.overhang(print_mesh(chamfered_cube(3, 30, height=3)), max_deg=45)
    assert result["area_mm2"] > 50


def test_a_face_exactly_at_the_limit_passes():
    assert pr.overhang(print_mesh(chamfered_cube(3, 45, height=3)), max_deg=45)["area_mm2"] == 0
    assert pr.overhang(print_mesh(chamfered_cube(3, 45, height=3)), max_deg=44)["area_mm2"] > 50
