"""The shipped examples and scaffold templates."""
import filecmp
from pathlib import Path

import pytest

from agentcad.cli import SCAFFOLD, TEMPLATES

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("example, template", [("bracket", "bracket"), ("box_with_lid", "box-with-lid")])
def test_examples_match_their_templates(example, template):
    for name in ("agentcad.toml", "model.py"):
        assert filecmp.cmp(EXAMPLES / example / name, SCAFFOLD / template / name, shallow=False), name


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_ships_a_readme_and_project_files(template):
    for name in ("agentcad.toml", "model.py", "README.md"):
        assert (SCAFFOLD / template / name).is_file(), name


def test_broken_fixture_is_labelled_as_negative():
    assert "NEGATIVE FIXTURE" in (EXAMPLES / "broken" / "model.py").read_text()


def test_sources_contain_no_em_dashes():
    root = EXAMPLES.parent
    for path in [*root.glob("src/agentcad/*.py"), *EXAMPLES.rglob("*.py"), *root.glob("docs/*.md")]:
        if path.name in ("render.py", "getting-started.md"):
            continue
        assert "—" not in path.read_text(), path


# Examples that must check clean, with the exit code `agentcad check` is expected to return.
CHECKED_EXAMPLES = [("flat_panel", 0), ("quadcopter", 0)]


@pytest.mark.parametrize("example, expected_exit", CHECKED_EXAMPLES)
def test_checked_examples_exit_with_the_expected_code(example, expected_exit, cli, copy_example):
    folder = copy_example(example)
    code, _, err = cli("check", folder)
    assert code == expected_exit, err


def test_flat_panel_exports_a_dxf_and_svg_for_the_laser_part(cli, copy_example):
    folder = copy_example("flat_panel")
    assert cli("build", folder)[0] == 0
    assert (folder / "build" / "dxf" / "panel.dxf").is_file()
    assert (folder / "build" / "svg" / "panel.svg").is_file()
    assert not (folder / "build" / "dxf" / "bracket.dxf").exists()
