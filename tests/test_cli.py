"""The CLI through subprocesses: output shape and exit codes."""
import json
import shutil

import cadquery as cq
import pytest
from helpers import parse


@pytest.fixture
def stls(tmp_path):
    """Three STL cubes: a at the origin, b overlapping it by 1 mm, c far away."""
    paths = {}
    for name, x in (("a", 0), ("b", 9), ("c", 100)):
        path = tmp_path / f"{name}.stl"
        cq.exporters.export(cq.Workplane("XY").box(10, 10, 10, centered=(True, True, False)).translate((x, 0, 0)), str(path))
        paths[name] = path
    return paths


def test_console_entry_point_runs_as_a_subprocess(cli_process, examples_dir, tmp_path):
    folder = tmp_path / "bracket"
    shutil.copytree(examples_dir / "bracket", folder, ignore=shutil.ignore_patterns("build"))
    code, out, err = cli_process("check", folder, "--json")
    assert code == 0 and err == "" and parse(out)["status"] == "PASS"
    broken = tmp_path / "bad"
    broken.mkdir()
    (broken / "agentcad.toml").write_text("[project]\nmodule = 'm.py'\n[bogus]\nx = 1\n")
    code, out, err = cli_process("check", broken, "--json")
    assert code == 2 and parse(out)["schema"] == "agentcad.error/1" and "bogus" in err
    assert cli_process("printers")[0] == 0


def test_check_good_examples_exit_zero_with_valid_json(cli, examples_dir, tmp_path):
    for name in ("box_with_lid", "bracket"):
        folder = tmp_path / name
        shutil.copytree(examples_dir / name, folder, ignore=shutil.ignore_patterns("build"))
        code, out, _ = cli("check", folder, "--json")
        document = parse(out)
        assert code == 0 and document["schema"] == "agentcad.report/1" and document["status"] == "PASS"
        assert (folder / "build" / "report.json").is_file()
        assert parse((folder / "build" / "report.json").read_text())["status"] == "PASS"


def test_check_broken_exits_one_with_every_failure(cli, examples_dir, tmp_path):
    folder = tmp_path / "broken"
    shutil.copytree(examples_dir / "broken", folder, ignore=shutil.ignore_patterns("build"))
    code, out, _ = cli("check", folder, "--json")
    document = parse(out)
    status = {(c["id"], c["part"]): c["status"] for c in document["checks"]}
    assert code == 1 and document["exit_code"] == 1
    assert status[("interference", "left+right")] == status[("overhang", "tee")] == "FAIL"
    assert status[("bed_fit", "long_bar")] == status[("min_wall", "thin_cup")] == status[("left_right_gap", "left+right")] == "FAIL"
    assert status[("check_boom", None)] == "ERROR"


def test_check_human_output_is_a_table(cli, examples_dir, tmp_path):
    folder = tmp_path / "bracket"
    shutil.copytree(examples_dir / "bracket", folder, ignore=shutil.ignore_patterns("build"))
    code, out, _ = cli("check", folder)
    assert code == 0 and out.splitlines()[-1].startswith("11 pass, 0 warn, 0 fail (build ") and out.startswith("PASS")


def test_check_only_and_no_build(cli, examples_dir, tmp_path):
    folder = tmp_path / "bracket"
    shutil.copytree(examples_dir / "bracket", folder, ignore=shutil.ignore_patterns("build"))
    code, _, err = cli("check", folder, "--no-build")
    assert code == 2 and "No fresh build" in err
    assert cli("build", folder)[0] == 0
    code, out, _ = cli("check", folder, "--no-build", "--only", "overhang", "--json")
    assert code == 0 and [c["id"] for c in parse(out)["checks"]] == ["overhang"]
    assert cli("check", folder, "--only", "nonsense")[0] == 2


def test_model_exception_exits_three_as_an_error_check(cli, make_project):
    folder = make_project("raise RuntimeError('model blew up')\n")
    code, out, _ = cli("check", folder, "--json")
    document = parse(out)
    assert code == 3 and document["checks"][0]["status"] == "ERROR" and "model blew up" in document["checks"][0]["summary"]
    code, out, err = cli("build", folder, "--json")
    assert code == 3 and parse(out)["schema"] == "agentcad.error/1" and "model blew up" in err


def test_broken_config_exits_two(cli, make_project):
    folder = make_project("def parts():\n    return []\n", "[checks]\nmax_overhang = 3\n")
    code, out, err = cli("check", folder, "--json")
    assert code == 2 and parse(out)["error"] == err.removeprefix("agentcad: error: ").strip()
    assert "checks.max_overhang" in err and "max_overhang_deg" in err


def test_missing_project_and_missing_file_exit_two(cli, tmp_path):
    assert cli("check", tmp_path / "nowhere")[0] == 2
    assert cli("measure", tmp_path / "missing.stl")[0] == 2


def test_build_prints_parts_and_json_manifest(cli, examples_dir, tmp_path):
    folder = tmp_path / "bracket"
    shutil.copytree(examples_dir / "bracket", folder, ignore=shutil.ignore_patterns("build"))
    code, out, _ = cli("build", folder)
    assert code == 0 and "bracket:" in out and out.strip().endswith("build")
    code, out, _ = cli("build", folder, "--json")
    assert code == 0 and parse(out)["schema"] == "agentcad.manifest/1"


def test_measure(cli, stls):
    code, out, _ = cli("measure", stls["a"], "--json")
    entry = parse(out)["checks"][0]
    assert code == 0 and entry["measured"]["volume_mm3"] == pytest.approx(1000)
    assert entry["measured"]["bbox"]["size_mm"] == pytest.approx([10, 10, 10])
    assert entry["measured"]["center_of_mass_mm"] == pytest.approx([0, 0, 5])


def test_interfere_exit_codes(cli, stls):
    code, out, _ = cli("interfere", stls["a"], stls["b"], "--json")
    assert code == 1 and parse(out)["checks"][0]["measured"]["overlap_mm3"] == pytest.approx(100, rel=1e-3)
    assert cli("interfere", stls["a"], stls["c"])[0] == 0
    assert cli("interfere", stls["a"], stls["b"], "--tol", "500")[0] == 0
    assert cli("interfere", stls["a"])[0] == 2


def test_overhang(cli, tmp_path):
    tee = (cq.Workplane("XY").box(10, 10, 25, centered=(True, True, False))
           .union(cq.Workplane("XY").workplane(offset=25).box(20, 20, 4, centered=(True, True, False))))
    path = tmp_path / "tee.stl"
    cq.exporters.export(tee, str(path))
    code, out, _ = cli("overhang", path, "--json")
    check = parse(out)["checks"][0]
    assert code == 1 and check["measured"]["area_mm2"] == pytest.approx(300, abs=0.5)
    assert cli("overhang", path, "--allow", "400")[0] == 0
    assert cli("overhang", path, "--max-deg", "90")[0] == 0


def test_wall_options(cli, stls):
    code, out, _ = cli("wall", stls["a"], "--samples", "3000", "--thin", "12", "--bands", "5", "--zmin", "0", "--zmax", "10",
                       "--rmin", "0", "--rmax", "50", "--json")
    measured = parse(out)["checks"][0]["measured"]
    assert code == 0 and measured["min_mm"] == pytest.approx(10, abs=0.1) and measured["thin_share"] == 1
    assert measured["bands"]
    code, out, _ = cli("wall", stls["a"], "--samples", "3000", "--bands", "5")
    assert code == 0 and "a.stl z" in out.splitlines()[-2]
    code, out, _ = cli("wall", stls["a"], "--samples", "3000", "--up", "--thin", "12", "--json")
    assert parse(out)["checks"][0]["measured"]["thin_share"] == 1


def test_fit(cli, stls):
    assert cli("fit", stls["a"], "--printer", "generic-180")[0] == 0
    code, out, _ = cli("fit", stls["a"], "--bed", "11x11x20", "--json")
    assert code == 1 and parse(out)["checks"][0]["status"] == "FAIL"
    assert cli("fit", stls["a"], "--bed", "50x50x50")[0] == 0
    assert cli("fit", stls["a"])[0] == 2
    assert cli("fit", stls["a"], "--printer", "nope")[0] == 2
    assert cli("fit", stls["a"], "--bed", "big")[0] == 2


def test_diff_of_two_builds(cli, make_project):
    old = make_project("import cadquery as cq\nfrom agentcad import Part\n"
                       "def parts():\n    return [Part('c', cq.Workplane('XY').box(10, 10, 10))]\n")
    new = make_project("import cadquery as cq\nfrom agentcad import Part\n"
                       "def parts():\n    return [Part('c', cq.Workplane('XY').box(10, 10, 12))]\n")
    assert cli("build", old)[0] == 0 and cli("build", new)[0] == 0
    code, out, _ = cli("diff", old / "build", new / "build" / "manifest.json", "--json")
    document = parse(out)
    assert code == 0 and document["changed"] and document["parts"]["changed"][0]["volume_mm3"]["delta"] == pytest.approx(200)
    assert cli("diff", old, new)[0] == 0  # project folders resolve to their build/
    assert cli("diff", old, old / "nothing.json")[0] == 2


def test_printers(cli):
    code, out, _ = cli("printers", "--json")
    names = [p["name"] for p in parse(out)["printers"]]
    assert code == 0 and "generic-220" in names
    assert "generic-350" in cli("printers")[1]


def test_new_scaffolds_each_template(cli, tmp_path):
    for template in ("blank", "bracket", "box-with-lid"):
        target = tmp_path / template
        code, out, _ = cli("new", target, "--template", template, "--json")
        assert code == 0 and "README.md" in parse(out)["files"] and (target / "model.py").is_file()
    assert cli("check", tmp_path / "blank")[0] == 0
    assert cli("check", tmp_path / "box-with-lid")[0] == 0


def test_new_defaults_to_blank_and_refuses_a_non_empty_folder(cli, tmp_path):
    assert cli("new", tmp_path / "fresh")[0] == 0 and "cube" in (tmp_path / "fresh" / "model.py").read_text()
    code, _, err = cli("new", tmp_path / "fresh")
    assert code == 2 and "not empty" in err
    (tmp_path / "file").write_text("x")
    assert cli("new", tmp_path / "file")[0] == 2


def test_doctor_and_version_and_usage(cli):
    code, out, _ = cli("doctor", "--json")
    assert code == 0 and {c["id"] for c in parse(out)["checks"]} >= {"python", "agentcad", "cadquery", "write_access"}
    assert cli("--version")[1].startswith("agentcad ")
    assert cli()[0] == 2
    assert cli("check", "--bogus")[0] == 2


def test_json_flag_prints_a_single_document(cli, examples_dir, tmp_path):
    folder = tmp_path / "bracket"
    shutil.copytree(examples_dir / "bracket", folder, ignore=shutil.ignore_patterns("build"))
    _, out, err = cli("check", folder, "--json")
    assert json.loads(out) and err == ""


def test_new_names_the_project_after_the_folder(cli, tmp_path):
    for template in ("blank", "bracket", "box-with-lid"):
        target = tmp_path / f"my {template}"
        assert cli("new", target, "--template", template)[0] == 0
        text = (target / "agentcad.toml").read_text()
        assert f'name = "my {template}"' in text and text.count("name =") == 1
        assert '[printer]' in text  # the rest of the config is untouched
    code, out, _ = cli("check", tmp_path / "my bracket", "--json")
    assert code == 0 and parse(out)["project"] == "my bracket"


def test_check_reports_build_and_check_seconds(cli, copy_example):
    folder = copy_example("bracket")
    code, out, _ = cli("check", folder, "--json")
    meta = parse(out)["meta"]
    assert code == 0 and meta["build_s"] >= 0 and meta["elapsed_s"] >= 0
    brief_meta = parse(cli("check", folder, "--json", "--brief")[1])["meta"]
    assert set(brief_meta) == {"elapsed_s", "build_s", "build"}
    table = cli("check", folder)[1].splitlines()[-1]
    assert "build " in table and "checks " in table


def test_brief_view_keeps_project_passes_as_compact_entries(cli, copy_example):
    data = parse(cli("check", copy_example("box_with_lid"), "--json", "--brief")[1])
    assert data["passed_checks"] and all(set(e) == {"id", "part", "summary"} for e in data["passed_checks"])
    assert any(e["id"] == "lid_play_x" for e in data["passed_checks"])
    assert "lid_play_x" not in data["passed"] and "valid" in data["passed"]
