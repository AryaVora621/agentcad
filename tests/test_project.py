"""agentcad.toml loading and isolated module import."""
import sys

import pytest

from agentcad.project import BuildError, ConfigError, Project

MODEL = '''
import cadquery as cq
from agentcad import Part

def parts():
    return [Part("cube", cq.Workplane("XY").box(10, 10, 10))]
'''


def test_defaults_and_loaded_parts(make_project):
    project = Project.load(make_project(MODEL))
    assert project.name == "t" and list(project.parts) == ["cube"]
    assert project.printer.name == "generic-220"
    assert project.checks["max_overhang_deg"] == 45 and project.export["tolerance"] == 0.02


def test_file_or_folder_path_both_work(make_project):
    folder = make_project(MODEL)
    assert Project.load(folder / "agentcad.toml").path == Project.load(folder).path


def test_unknown_key_names_the_key_and_the_allowed_keys(make_project):
    folder = make_project(MODEL, "[checks]\nmin_wal_mm = 1\n")
    with pytest.raises(ConfigError) as error:
        Project.load(folder)
    message = str(error.value)
    assert "checks.min_wal_mm" in message and "min_wall_mm" in message and "allowed" in message


def test_unknown_section_is_rejected(make_project):
    with pytest.raises(ConfigError, match="Unknown key 'printr'"):
        Project.load(make_project(MODEL, "[printr]\nprofile = 'x'\n"))


def test_missing_module_is_a_config_error(make_project):
    folder = make_project(MODEL)
    (folder / "model.py").unlink()
    with pytest.raises(ConfigError, match="module not found"):
        Project.load(folder)


def test_missing_config_and_missing_module_key(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        Project.load(tmp_path)
    (tmp_path / "agentcad.toml").write_text("[project]\nname = 'x'\n")
    with pytest.raises(ConfigError, match="project.module is required"):
        Project.load(tmp_path)


def test_invalid_toml_and_values(make_project, tmp_path):
    (tmp_path / "agentcad.toml").write_text("[project\n")
    with pytest.raises(ConfigError, match="Invalid TOML"):
        Project.load(tmp_path)
    with pytest.raises(ConfigError, match="non-negative"):
        Project.load(make_project(MODEL, "[checks]\nmin_wall_mm = -1\n"))
    with pytest.raises(ConfigError, match="max_overhang_deg"):
        Project.load(make_project(MODEL, "[checks]\nmax_overhang_deg = 120\n"))
    with pytest.raises(ConfigError, match="allow_overlap"):
        Project.load(make_project(MODEL, "[checks]\nallow_overlap = [['a']]\n"))
    with pytest.raises(ConfigError, match="unknown part"):
        Project.load(make_project(MODEL, "[checks]\nallow_overlap = [['cube', 'ghost']]\n"))


def test_printer_profile_and_bed_override(make_project):
    assert Project.load(make_project(MODEL, "[printer]\nprofile = 'generic-300'\n")).printer.bed_xyz == (300, 300, 300)
    custom = Project.load(make_project(MODEL, "[printer]\nbed = [350, 340, 330]\n")).printer
    assert custom.bed_xyz == (350, 340, 330) and custom.name == "custom"
    with pytest.raises(ConfigError, match="Unknown printer"):
        Project.load(make_project(MODEL, "[printer]\nprofile = 'nope'\n"))
    with pytest.raises(ConfigError, match="printer.bed"):
        Project.load(make_project(MODEL, "[printer]\nbed = [1, 2]\n"))


def test_model_errors_are_build_errors(make_project):
    with pytest.raises(BuildError, match="ZeroDivisionError"):
        Project.load(make_project("1 / 0\n"))
    with pytest.raises(BuildError, match="parts"):
        Project.load(make_project("x = 1\n"))
    with pytest.raises(BuildError, match="no parts"):
        Project.load(make_project("def parts():\n    return []\n"))
    with pytest.raises(BuildError, match="Part objects"):
        Project.load(make_project("def parts():\n    return [1]\n"))
    with pytest.raises(BuildError, match="unique"):
        Project.load(make_project(MODEL.replace("return [", "return [Part('cube', cq.Workplane('XY').box(1,1,1)), ")))


def test_sibling_imports_work_and_sys_path_is_restored(make_project):
    before = list(sys.path)
    helper = "import cadquery as cq\nfrom agentcad import Part\ndef make():\n    return Part('cube', cq.Workplane('XY').box(5, 5, 5))\n"
    folder = make_project("from helper import make\ndef parts():\n    return [make()]\n", files={"helper.py": helper})
    project = Project.load(folder)
    assert list(project.parts) == ["cube"]
    assert sys.path == before
    assert not [name for name in sys.modules if name.startswith("_agentcad_model_")]


def test_each_load_gets_a_fresh_namespace(make_project):
    folder = make_project("COUNT = []\nCOUNT.append(1)\ndef parts():\n    import cadquery as cq\n"
                          "    from agentcad import Part\n    return [Part('c', cq.Workplane('XY').box(1, 1, 1))]\n")
    first, second = Project.load(folder), Project.load(folder)
    assert first.module is not second.module and first.module.COUNT == [1] == second.module.COUNT
