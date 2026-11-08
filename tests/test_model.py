"""Pose and Part frame rules."""
import cadquery as cq
import pytest

from agentcad import Part, Pose


def moved(pose: Pose, point: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = pose.apply(cq.Vertex.makeVertex(*point)).toTuple()
    return round(x, 6), round(y, 6), round(z, 6)


def test_rx_90_maps_y_to_z():
    assert moved(Pose(rx=90), (0, 1, 0)) == (0, 0, 1)


def test_rz_90_maps_x_to_y():
    assert moved(Pose(rz=90), (1, 0, 0)) == (0, 1, 0)


def test_rotations_apply_x_then_y_then_z_about_the_origin():
    # Z first would send +Y to -X and leave it there; X first sends it to +Z, which Z then keeps.
    assert moved(Pose(rx=90, rz=90), (0, 1, 0)) == (0, 0, 1)
    assert moved(Pose(ry=90, rz=90), (0, 0, 1)) == (0, 1, 0)


def test_translation_happens_after_rotation():
    assert moved(Pose(x=5, rz=90), (1, 0, 0)) == (5, 1, 0)


def test_translation_moves_the_bbox():
    box = cq.Workplane("XY").box(10, 10, 10).val()
    before, after = box.BoundingBox(), Pose(x=5).apply(box).BoundingBox()
    assert after.xmin - before.xmin == pytest.approx(5)
    assert after.ymin == pytest.approx(before.ymin)


def test_apply_does_not_mutate_the_input():
    box = cq.Workplane("XY").box(10, 10, 10).val()
    Pose(x=50, rz=45).apply(box)
    assert box.BoundingBox().xmin == pytest.approx(-5)


def offset_slab() -> cq.Workplane:
    return cq.Workplane("XY").box(10, 20, 30, centered=False).translate((100, 100, 100))


def test_model_frame_is_the_shape_as_given():
    assert Part("slab", offset_slab()).solid("model").BoundingBox().xmin == pytest.approx(100)


def test_use_frame_applies_the_use_pose():
    box = Part("slab", offset_slab(), use=Pose(z=7)).solid("use").BoundingBox()
    assert box.zmin == pytest.approx(107)


def test_print_frame_sits_on_the_bed_and_is_centred():
    box = Part("slab", offset_slab(), print=Pose(rx=90)).solid("print").BoundingBox()
    assert box.zmin == pytest.approx(0, abs=1e-9)
    assert box.zlen == pytest.approx(20)
    assert (box.xmin + box.xmax) / 2 == pytest.approx(0, abs=1e-9)
    assert (box.ymin + box.ymax) / 2 == pytest.approx(0, abs=1e-9)


def test_print_frame_ignores_print_translation():
    plain = Part("slab", offset_slab()).solid("print").BoundingBox()
    shifted = Part("slab", offset_slab(), print=Pose(x=50, y=-9, z=300)).solid("print").BoundingBox()
    assert (shifted.xmin, shifted.ymin, shifted.zmin) == pytest.approx((plain.xmin, plain.ymin, plain.zmin))


def test_unknown_frame_is_rejected():
    with pytest.raises(ValueError, match="frame"):
        Part("slab", offset_slab()).solid("world")


@pytest.mark.parametrize("name", ["Lid", "my part", "", "a/b"])
def test_part_name_must_be_a_slug(name):
    with pytest.raises(ValueError, match="part name"):
        Part(name, offset_slab())


def test_part_validates_qty_print_and_color():
    with pytest.raises(ValueError, match="qty"):
        Part("a", offset_slab(), qty=0)
    with pytest.raises(ValueError, match="print"):
        Part("a", offset_slab(), print="flip")
    with pytest.raises(ValueError, match="color"):
        Part("a", offset_slab(), color="red")


def test_public_api_is_importable_from_the_package():
    import agentcad

    for name in ("Part", "Pose", "Project", "Check", "Report", "Status", "CheckContext", "__version__"):
        assert hasattr(agentcad, name)


def test_cut_is_one_of_the_three_processes_or_none():
    assert Part("a", offset_slab()).cut is None
    for process in ("laser", "cnc", "waterjet"):
        assert Part("a", offset_slab(), cut=process).cut == process
    with pytest.raises(ValueError, match="cut must be 'laser', 'cnc', 'waterjet'"):
        Part("a", offset_slab(), cut="plasma")
    with pytest.raises(ValueError, match="cut must be"):
        Part("a", offset_slab(), cut="")
