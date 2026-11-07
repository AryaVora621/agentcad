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
