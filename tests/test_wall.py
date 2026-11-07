"""Wall thickness against shapes with known walls."""
import cadquery as cq
import pytest
from helpers import print_mesh

from agentcad.wall import wall_thickness

SAMPLES = 8000


def cup(wall: float):
    return print_mesh(cq.Workplane("XY").box(30, 30, 20, centered=(True, True, False)).faces(">Z").shell(-wall))


def test_hollow_box_minimum_matches_the_wall():
    result = wall_thickness(cup(1.0), SAMPLES)
    # The ray starts 0.01 mm inside the surface (as in stlwall), so a 1.0 mm wall reads 0.99.
    assert result["min_mm"] == pytest.approx(1.0, abs=0.05)
    assert result["p1_mm"] == pytest.approx(1.0, abs=0.05)


def test_thin_share_counts_the_surface_under_the_threshold():
    plate = print_mesh(cq.Workplane("XY").box(40, 40, 2, centered=(True, True, False)))
    result = wall_thickness(plate, 20000, thin=3.0)
    # Top and bottom (3200 mm2) are 2 mm thick, the four edges (320 mm2) are 40 mm thick.
    assert result["thin_share"] == pytest.approx(3200 / 3520, abs=0.03)
    assert result["thin_z_mm"][0] == pytest.approx(0, abs=0.01)
    assert wall_thickness(plate, 20000, thin=1.0)["thin_share"] == 0


def test_filters_select_samples():
    plate = print_mesh(cq.Workplane("XY").box(40, 40, 2, centered=(True, True, False)))
    top = wall_thickness(plate, 20000, up=True, thin=3.0)
    assert top["thin_share"] == 1 and top["samples"] < 12000
    assert wall_thickness(plate, 20000, zmin=1.9)["samples"] < wall_thickness(plate, 20000)["samples"]
    centre = wall_thickness(plate, 20000, rmax=5.0, thin=3.0)
    assert centre["thin_radius_mm"][1] <= 5.0


def test_bands_are_tabulated_per_height():
    result = wall_thickness(cup(1.0), SAMPLES, bands=5.0)
    assert len(result["bands"]) >= 3
    first = result["bands"][0]
    assert first["z_mm"] == [0.0, 5.0] and first["min_mm"] <= first["median_mm"]


def test_runs_are_deterministic():
    mesh = cup(1.2)
    assert wall_thickness(mesh, SAMPLES, thin=2) == wall_thickness(mesh, SAMPLES, thin=2)


def test_empty_selection_has_no_hits():
    result = wall_thickness(cup(1.0), SAMPLES, zmin=500)
    assert result["hits"] == 0 and result["min_mm"] is None


def test_rejects_bad_arguments():
    with pytest.raises(ValueError):
        wall_thickness(cup(1.0), 0)
