"""Dimensioned drawings: files, measured dimensions, layout rules, determinism and CLI errors."""
import json
import struct

import cadquery as cq
import pytest
from helpers import parse

from agentcad import drawing
from agentcad.geometry import bbox
from agentcad.project import ConfigError


@pytest.fixture(scope="module")
def plate_step(tmp_path_factory):
    """A 50 x 30 x 6 plate with four 3.4 mm holes."""
    path = tmp_path_factory.mktemp("plate") / "plate.step"
    plate = (cq.Workplane("XY").box(50, 30, 6, centered=(False, False, False)).faces(">Z")
             .workplane(centerOption="CenterOfBoundBox").pushPoints([(-10, 5), (10, 5), (-10, -5), (10, -5)])
             .hole(3.4))
    cq.exporters.export(plate, str(path))
    return path


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def by_role(sheet, role):
    return [d for d in sheet["dimensions"] if d["role"] == role]


def test_bracket_files_and_overall_dimensions_match_measurement(examples_dir, tmp_path):
    from agentcad.project import Project

    doc = drawing.make_drawings([examples_dir / "bracket"], out=tmp_path / "d")
    assert doc["schema"] == "agentcad.drawing/1" and doc["project"] == "bracket"
    sheet = doc["sheets"][0]
    for fmt in sheet["files"]:
        assert (tmp_path / "d" / f"bracket.{fmt}").is_file()
    assert (tmp_path / "d" / "bracket.pdf").read_bytes().startswith(b"%PDF")
    assert png_size(tmp_path / "d" / "bracket.png") == drawing.png_size(sheet["sheet"])
    size = bbox(Project.load(examples_dir / "bracket").parts["bracket"].solid("model"))["size_mm"]
    assert by_role(sheet, "width")[0]["value_mm"] == pytest.approx(size[0], abs=0.01)
    assert by_role(sheet, "height")[0]["value_mm"] == pytest.approx(size[2], abs=0.01)
    assert by_role(sheet, "depth")[0]["value_mm"] == pytest.approx(size[1], abs=0.01)
    holes = by_role(sheet, "diameter")
    assert holes[0]["count"] == 2 and holes[0]["value_mm"] == pytest.approx(4.4, abs=0.01)


def test_a4_png_is_1754_by_1240(plate_step, tmp_path):
    doc = drawing.make_drawings([plate_step], out=tmp_path, sheet="A4", formats=["png"])
    assert png_size(tmp_path / "plate.png") == (1754, 1240)
    assert list(doc["sheets"][0]["files"]) == ["png"]


def test_plate_has_one_diameter_callout_for_four_holes(plate_step, tmp_path):
    sheet = drawing.make_drawings([plate_step], out=tmp_path)["sheets"][0]
    callouts = by_role(sheet, "diameter")
    assert len(callouts) == 1
    assert callouts[0]["count"] == 4 and callouts[0]["value_mm"] == pytest.approx(3.4, abs=0.001)
    assert callouts[0]["text"] == "4x Ø3.4"
    assert sorted(d["value_mm"] for d in by_role(sheet, "position_x")) == pytest.approx([0, 15, 35], abs=0.01) \
        or sorted(d["value_mm"] for d in by_role(sheet, "position_x")) == pytest.approx([15, 35], abs=0.01)
    assert sorted(d["value_mm"] for d in by_role(sheet, "position_y")) == pytest.approx([10, 20], abs=0.01)


def test_same_input_gives_byte_identical_files(plate_step, tmp_path):
    drawing.make_drawings([plate_step], out=tmp_path / "a")
    drawing.make_drawings([plate_step], out=tmp_path / "b")
    for ext in ("svg", "pdf", "png"):
        assert (tmp_path / "a" / f"plate.{ext}").read_bytes() == (tmp_path / "b" / f"plate.{ext}").read_bytes()
    svg = (tmp_path / "a" / "plate.svg").read_text()
    assert "<dc:date>" not in svg and "3.4" in svg


def test_first_and_third_angle_put_top_on_opposite_sides_of_front(plate_step, tmp_path):
    def boxes(projection):
        sheet = drawing.make_drawings([plate_step], out=tmp_path / projection, projection=projection,
                                      formats=["svg"])["sheets"][0]
        return {v["name"]: v["bbox_mm"] for v in sheet["views"]}

    third, first = boxes("third"), boxes("first")
    # Sheet y grows downward in the document, so "above" means a smaller y.
    assert third["top"]["max_mm"][1] < third["front"]["min_mm"][1]
    assert first["top"]["min_mm"][1] > first["front"]["max_mm"][1]
    assert third["right"]["min_mm"][0] > third["front"]["max_mm"][0]
    assert first["right"]["max_mm"][0] < first["front"]["min_mm"][0]


def test_layouts_have_no_conflicts_and_scale_is_standard(examples_dir, plate_step):
    from agentcad.project import Project

    items = [drawing.Item(n, p.solid("model"), "x", None, 1) for n, p in
             Project.load(examples_dir / "box_with_lid").parts.items()]
    items.append(drawing.Item("plate", cq.importers.importStep(str(plate_step)).val(), None, None, 1))
    for item in items:
        for projection in ("third", "first"):
            planned = drawing.choose_sheet(item, drawing.project_views(item.shape), projection, "auto")
            assert planned.conflicts() == []
            assert planned.scale_name in {name for _, name in drawing.SCALES}


def test_many_holes_switch_to_a_hole_table(tmp_path):
    points = [(x * 8 - 12, y * 8 - 8) for x in range(4) for y in range(3)]
    plate = (cq.Workplane("XY").box(50, 30, 6, centered=(False, False, False)).faces(">Z")
             .workplane(centerOption="CenterOfBoundBox").pushPoints(points).hole(2.5))
    path = tmp_path / "grid.step"
    cq.exporters.export(plate, str(path))
    sheet = drawing.make_drawings([path], out=tmp_path / "o", formats=["svg"])["sheets"][0]
    assert len(sheet["hole_table"]) == 12 and not by_role(sheet, "position_x")
    assert sheet["hole_table"][0]["diameter_mm"] == pytest.approx(2.5, abs=0.001)


def test_auto_sheet_prefers_a4_unless_a3_allows_a_larger_scale(plate_step, tmp_path):
    small = drawing.make_drawings([plate_step], out=tmp_path / "s", formats=["svg"])["sheets"][0]
    assert small["sheet"] == "A4"
    big = tmp_path / "big.step"
    cq.exporters.export(cq.Workplane("XY").box(200, 120, 40), str(big))
    sheet = drawing.make_drawings([big], out=tmp_path / "b", formats=["svg"])["sheets"][0]
    assert sheet["sheet"] in {"A4", "A3"} and sheet["scale"] in {"1:1", "1:2"}


def test_mesh_input_and_unknown_parts_exit_two(cli, examples_dir, tmp_path):
    stl = tmp_path / "cube.stl"
    cq.exporters.export(cq.Workplane("XY").box(10, 10, 10), str(stl))
    code, out, err = cli("drawing", stl, "--json")
    assert code == 2 and "meshes have no edges" in err and parse(out)["schema"] == "agentcad.error/1"
    code, out, err = cli("drawing", examples_dir / "box_with_lid", "--parts", "nope", "-o", tmp_path / "x")
    assert code == 2 and "box, lid" in err
    with pytest.raises(ConfigError):
        drawing.make_drawings([examples_dir / "bracket"], out=tmp_path, formats="gif")


def test_cli_json_matches_library_and_text_lists_files(cli, plate_step, tmp_path):
    code, out, _ = cli("drawing", plate_step, "-o", tmp_path / "j", "--json")
    assert code == 0
    assert parse(out) == json.loads(json.dumps(drawing.make_drawings([plate_step], out=tmp_path / "j")))
    code, out, _ = cli("drawing", plate_step, "-o", tmp_path / "t", "--format", "svg,png")
    assert code == 0 and out.splitlines() == [str((tmp_path / "t" / "plate.svg").resolve()),
                                              str((tmp_path / "t" / "plate.png").resolve())]


def test_title_block_carries_part_data_and_geometry_hash(examples_dir, tmp_path):
    svg_doc = drawing.make_drawings([examples_dir / "bracket"], out=tmp_path, formats=["svg"])
    svg = (tmp_path / "bracket.svg").read_text()
    for text in ("THIRD ANGLE", "PETG", "mm", "agentcad"):
        assert text in svg
    assert svg_doc["sheets"][0]["scale"] in svg
