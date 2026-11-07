"""Tests for agentcad.render."""

import os
from pathlib import Path

import numpy as np
import pytest
import trimesh
import vtk
from vtk.util import numpy_support

from agentcad.render import (
    _apply_section,
    _label_lines,
    _trimesh_to_polydata,
    offscreen_ok,
    render_sheet,
)


@pytest.mark.render
def test_offscreen_ok() -> None:
    ok, msg = offscreen_ok()
    assert ok, f"Offscreen rendering failed: {msg}"
    assert "VTK" in msg
    assert "offscreen OK" in msg


@pytest.mark.render
def test_render_sheet_basic(tmp_path: Path) -> None:
    ok, _ = offscreen_ok()
    if not ok:
        pytest.skip("Offscreen VTK not available")

    # Create test meshes: box and cylinder
    box = trimesh.creation.box(extents=[20, 30, 10])
    cylinder = trimesh.creation.cylinder(radius=5, height=15)

    items = [
        ("box", box, "#ff0000"),
        ("cylinder", cylinder, "#00ff00"),
    ]

    out_dir = tmp_path / "renders"
    paths = render_sheet(items, str(out_dir), views=("iso", "front", "right", "top"))

    # Check all expected files exist
    expected = ["iso.png", "front.png", "right.png", "top.png", "sheet.png"]
    for name in expected:
        p = out_dir / name
        assert p.exists(), f"Missing {name}"
        assert p.stat().st_size > 0, f"Empty file: {name}"

    # Check returned paths match
    assert len(paths) == 5
    assert paths[-1].endswith("sheet.png")

    # Check image dimensions
    import vtk
    for name in ["iso.png", "front.png", "right.png", "top.png"]:
        reader = vtk.vtkPNGReader()
        reader.SetFileName(str(out_dir / name))
        reader.Update()
        img = reader.GetOutput()
        assert img.GetDimensions() == (1600, 1200, 1), f"{name} wrong size: {img.GetDimensions()}"

    # Sheet should be 2x2 grid = 3200 x 2400
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(out_dir / "sheet.png"))
    reader.Update()
    img = reader.GetOutput()
    assert img.GetDimensions() == (3200, 2400, 1), f"sheet.png wrong size: {img.GetDimensions()}"

    # Check images are not flat color (std dev > threshold)
    for name in ["iso.png", "front.png", "right.png", "top.png"]:
        reader = vtk.vtkPNGReader()
        reader.SetFileName(str(out_dir / name))
        reader.Update()
        img = reader.GetOutput()
        scalars = img.GetPointData().GetScalars()
        from vtk.util import numpy_support
        arr = numpy_support.vtk_to_numpy(scalars)
        w, h, _ = img.GetDimensions()
        components = scalars.GetNumberOfComponents()
        arr = arr.reshape(h, w, components)
        # Check RGB channels have variation
        std_rgb = arr[:, :, :3].std()
        assert std_rgb > 5.0, f"{name} appears to be flat color (std={std_rgb:.1f})"


@pytest.mark.render
def test_render_sheet_section(tmp_path: Path) -> None:
    ok, _ = offscreen_ok()
    if not ok:
        pytest.skip("Offscreen VTK not available")

    box = trimesh.creation.box(extents=[20, 20, 20])
    items = [("box", box, "#ff0000")]

    out_dir = tmp_path / "renders_section"
    _paths = render_sheet(items, str(out_dir), views=("iso", "front"), section=("z", 5.0))

    expected = ["iso.png", "front.png", "sheet.png"]
    for name in expected:
        p = out_dir / name
        assert p.exists(), f"Missing {name}"

    # Section view should be labeled accordingly
    import vtk
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(out_dir / "iso.png"))
    reader.Update()
    img = reader.GetOutput()
    assert img.GetDimensions() == (1600, 1200, 1)


@pytest.mark.render
def test_render_sheet_deterministic(tmp_path: Path) -> None:
    ok, _ = offscreen_ok()
    if not ok:
        pytest.skip("Offscreen VTK not available")

    box = trimesh.creation.box(extents=[10, 10, 10])
    items = [("box", box, None)]

    out_dir1 = tmp_path / "run1"
    out_dir2 = tmp_path / "run2"

    # Call twice in same process
    paths1 = render_sheet(items, str(out_dir1), views=("iso", "front"))
    paths2 = render_sheet(items, str(out_dir2), views=("iso", "front"))

    # Both should succeed
    for p in paths1 + paths2:
        assert os.path.exists(p), f"Missing output: {p}"

    # Images should be identical (deterministic)
    import vtk
    for name in ["iso.png", "front.png", "sheet.png"]:
        reader1 = vtk.vtkPNGReader()
        reader1.SetFileName(str(out_dir1 / name))
        reader1.Update()
        reader2 = vtk.vtkPNGReader()
        reader2.SetFileName(str(out_dir2 / name))
        reader2.Update()
        img1 = reader1.GetOutput()
        img2 = reader2.GetOutput()
        # Compare pixel data
        scalars1 = img1.GetPointData().GetScalars()
        scalars2 = img2.GetPointData().GetScalars()
        assert scalars1.GetNumberOfTuples() == scalars2.GetNumberOfTuples()
        for i in range(scalars1.GetNumberOfTuples()):
            assert tuple(scalars1.GetTuple(i)) == tuple(scalars2.GetTuple(i)), f"Pixel {i} differs in {name}"


@pytest.mark.render
def test_render_sheet_default_colors(tmp_path: Path) -> None:
    ok, _ = offscreen_ok()
    if not ok:
        pytest.skip("Offscreen VTK not available")

    box = trimesh.creation.box(extents=[10, 10, 10])
    cylinder = trimesh.creation.cylinder(radius=5, height=10)

    # No colors provided - should use default palette
    items = [
        ("box", box, None),
        ("cylinder", cylinder, None),
    ]

    out_dir = tmp_path / "renders_default"
    _paths = render_sheet(items, str(out_dir), views=("iso",))

    assert (out_dir / "iso.png").exists()
    assert (out_dir / "sheet.png").exists()


@pytest.mark.render
def test_render_sheet_opacity(tmp_path: Path) -> None:
    ok, _ = offscreen_ok()
    if not ok:
        pytest.skip("Offscreen VTK not available")

    box = trimesh.creation.box(extents=[10, 10, 10])
    items = [("box", box, "#ff0000@0.5")]  # Opacity in hex not supported, but shouldn't crash

    out_dir = tmp_path / "renders_opacity"
    _paths = render_sheet(items, str(out_dir), views=("iso",))

    assert (out_dir / "iso.png").exists()


def _read(path: Path) -> np.ndarray:
    """Read a PNG as a (h, w, c) array with the top row first, as it displays."""
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(path))
    reader.Update()
    img = reader.GetOutput()
    w, h, _ = img.GetDimensions()
    scalars = img.GetPointData().GetScalars()
    arr = numpy_support.vtk_to_numpy(scalars).reshape(h, w, scalars.GetNumberOfComponents())
    return arr[::-1]


def _require_offscreen() -> None:
    ok, _ = offscreen_ok()
    if not ok:
        pytest.skip("Offscreen VTK not available")


SIZES = [(800, 600), (1600, 1200)]


def _box_top_view(tmp_path: Path, size: tuple[int, int] = (800, 600)) -> np.ndarray:
    box = trimesh.creation.box(extents=[60, 40, 30])
    render_sheet([("box", box, "#4a7bd0")], str(tmp_path), views=("top",), size=size)
    return _read(tmp_path / "top.png")[:, :, :3].astype(int)


@pytest.mark.render
@pytest.mark.parametrize("size", SIZES)
def test_scale_bar_matches_viewport(tmp_path: Path, size: tuple[int, int]) -> None:
    """Bar px/mm must equal the model px/mm (60 mm box, top view) within 3%."""
    _require_offscreen()
    w, h = size
    top = _box_top_view(tmp_path, size)

    blue = top[:, :, 2] > top[:, :, 0] + 40
    cols = np.nonzero(blue.any(axis=0))[0]
    model_px_per_mm = (cols.max() - cols.min() + 1) / 60.0

    dark = top.sum(axis=2) < 150
    dark[:, : w // 2] = False
    dark[: h // 2, :] = False
    best = 0
    for row in np.unique(np.nonzero(dark)[0]):
        cs = np.nonzero(dark[row])[0]
        runs = np.split(cs, np.where(np.diff(cs) != 1)[0] + 1)
        best = max(best, max(len(r) for r in runs))
    assert best > 0, "scale bar not found in the lower right"

    # The bar is a round length in mm; pick the one nearest to the measured run.
    length = min((10, 20, 50), key=lambda mm: abs(best - mm * model_px_per_mm))
    assert abs(best / (length * model_px_per_mm) - 1.0) < 0.03


@pytest.mark.render
@pytest.mark.parametrize("size", SIZES)
def test_model_clear_of_label_bands(tmp_path: Path, size: tuple[int, int]) -> None:
    """No model pixel in the top 140 s px, bottom 90 s px or outer 5% of the width."""
    _require_offscreen()
    w, h = size
    s = h / 600
    box = trimesh.creation.box(extents=[60, 40, 30])
    render_sheet([("box", box, "#4a7bd0")], str(tmp_path), size=size)
    for view in ("iso", "front", "right", "top"):
        img = _read(tmp_path / f"{view}.png")[:, :, :3].astype(int)
        blue = img[:, :, 2] > img[:, :, 0] + 40
        rows, cols = np.nonzero(blue)
        assert rows.min() >= 140 * s, f"{view} model reaches row {rows.min()}"
        assert rows.max() <= h - 90 * s, f"{view} model reaches row {rows.max()}"
        assert cols.min() >= 0.05 * w and cols.max() <= 0.95 * w, f"{view} too close to the sides"
        assert abs((cols.min() + cols.max()) / 2 - w / 2) < 0.0125 * w, f"{view} not centred"


@pytest.mark.render
def test_label_font_scales_with_view_size() -> None:
    size = (60.0, 40.0, 30.0)
    small = [f for _, f, _ in _label_lines("iso", "t", size, ("z", 1.0), 1.0)]
    large = [f for _, f, _ in _label_lines("iso", "t", size, ("z", 1.0), 1200 / 600)]
    assert large == [2 * f for f in small]


@pytest.mark.render
def test_section_label_lines() -> None:
    """The section line is added only when a section is set; the view name appears once."""
    size = (60.0, 40.0, 30.0)
    plain = [t for t, _, _ in _label_lines("iso", None, size, None)]
    assert plain == ["iso", "60.0 x 40.0 x 30.0 mm"]
    cut = [t for t, _, _ in _label_lines("iso", "part", size, ("y", 0.0))]
    assert cut[0] == "iso"
    assert cut.count("iso") == 1
    assert cut[-1] == "section y=0.0 mm"


@pytest.mark.render
def test_section_cut_keeps_correct_half() -> None:
    """Section cuts keep the side facing away from the cameras so the cut face shows."""
    pd = _trimesh_to_polydata(trimesh.creation.box(extents=[20, 20, 20]))

    b = _apply_section(pd, "z", 5.0).GetBounds()
    assert abs(b[5] - 5.0) < 1e-6 and abs(b[4] + 10.0) < 1e-6
    assert abs(_apply_section(pd, "x", 0.0).GetBounds()[1]) < 1e-6
    assert abs(_apply_section(pd, "y", 0.0).GetBounds()[2]) < 1e-6


@pytest.mark.render
def test_section_render_differs(tmp_path: Path) -> None:
    _require_offscreen()
    items = [("box", trimesh.creation.box(extents=[20, 20, 20]), "#ff0000")]
    render_sheet(items, str(tmp_path / "cut"), views=("iso",), section=("y", 0.0))
    render_sheet(items, str(tmp_path / "plain"), views=("iso",))
    assert not np.array_equal(_read(tmp_path / "cut" / "iso.png"), _read(tmp_path / "plain" / "iso.png"))


@pytest.mark.render
def test_sheet_grid_order(tmp_path: Path) -> None:
    """Sheet reads iso, front on the top row and right, top on the bottom row."""
    _require_offscreen()
    items = [("box", trimesh.creation.box(extents=[20, 20, 20]), "#ff0000")]
    render_sheet(items, str(tmp_path), views=("iso", "front", "right", "top"), size=(200, 150))

    sheet = _read(tmp_path / "sheet.png")
    assert sheet.shape[:2] == (300, 400)
    assert np.array_equal(sheet[:150, :200], _read(tmp_path / "iso.png"))
    assert np.array_equal(sheet[:150, 200:], _read(tmp_path / "front.png"))
    assert np.array_equal(sheet[150:, :200], _read(tmp_path / "right.png"))
    assert np.array_equal(sheet[150:, 200:], _read(tmp_path / "top.png"))
