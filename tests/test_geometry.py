"""Known answers from primitives."""
import cadquery as cq
import pytest

from agentcad import geometry as g


@pytest.fixture
def block() -> cq.Shape:
    return cq.Workplane("XY").box(10, 20, 30, centered=False).val()


def test_volume_area_and_bbox(block):
    assert g.volume(block) == pytest.approx(6000)
    assert g.area(block) == pytest.approx(2200)
    box = g.bbox(block)
    assert box["min_mm"] == pytest.approx([0, 0, 0])
    assert box["max_mm"] == pytest.approx([10, 20, 30])
    assert box["size_mm"] == pytest.approx([10, 20, 30])


def test_center_of_mass(block):
    assert g.center_of_mass(block) == pytest.approx([5, 10, 15])


def test_validity_and_solid_count(block):
    assert g.is_valid(block) and g.solids_count(block) == 1
    face = cq.Face.makePlane(10, 10)
    assert not g.is_valid(face) and g.solids_count(face) == 0


def test_to_shape_keeps_every_solid_of_a_workplane():
    two = cq.Workplane("XY").pushPoints([(0, 0), (30, 0)]).box(10, 10, 10)
    shape = g.to_shape(two)
    assert g.solids_count(shape) == 2
    assert g.volume(shape) == pytest.approx(2000)


def test_to_shape_rejects_other_types():
    with pytest.raises(TypeError):
        g.to_shape("box")
    with pytest.raises(ValueError, match="no shapes"):
        g.to_shape(cq.Workplane("XY"))


def test_mesh_matches_the_solid(block):
    mesh = g.to_mesh(block)
    assert mesh.is_watertight
    assert g.volume(mesh) == pytest.approx(6000)
    assert g.area(mesh) == pytest.approx(2200)
    assert g.center_of_mass(mesh) == pytest.approx([5, 10, 15])
    assert g.is_valid(mesh) and g.solids_count(mesh) == 1


def test_load_round_trips_step_and_stl(block, tmp_path):
    cq.exporters.export(block, str(tmp_path / "b.step"))
    cq.exporters.export(block, str(tmp_path / "b.stl"))
    assert g.volume(g.load(tmp_path / "b.step")) == pytest.approx(6000)
    assert g.volume(g.load(tmp_path / "b.stl")) == pytest.approx(6000)


def test_load_rejects_missing_and_unknown_files(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        g.load(tmp_path / "nope.stl")
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(ValueError, match="Unsupported"):
        g.load(tmp_path / "x.txt")


def test_place_moves_a_copy(block):
    from agentcad import Pose
    moved = g.place(block, Pose(x=5))
    assert g.bbox(moved)["min_mm"][0] == pytest.approx(5)
    assert g.bbox(block)["min_mm"][0] == pytest.approx(0)
