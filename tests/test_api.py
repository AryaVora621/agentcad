"""Tests for agentcad.api: describe(), cheat_sheet(), and cli()."""
import argparse
import json
import subprocess

from agentcad import api, checks, cli, printers, project, report


def test_describe_has_schema_and_version():
    doc = api.describe()
    assert doc["schema"] == "agentcad.api/1"
    assert doc.get("agentcad")


def test_describe_commands_match_cli_subparsers():
    doc = api.describe()
    parser = cli._parser()
    subparsers_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    cli_commands = set()
    for action in subparsers_actions:
        for name in action.choices:
            if name not in ("-h", "--help"):
                cli_commands.add(name)
    api_commands = {c["name"] for c in doc["commands"]}
    assert api_commands == cli_commands, f"mismatch: CLI {cli_commands - api_commands}, API {api_commands - cli_commands}"


def test_describe_every_command_has_args_and_help():
    doc = api.describe()
    for cmd in doc["commands"]:
        assert cmd["name"]
        assert cmd["help"]
        assert isinstance(cmd["args"], list)
        for arg in cmd["args"]:
            assert arg.get("flags")
            assert "help" in arg


def test_describe_exit_codes():
    doc = api.describe()
    codes = doc["exit_codes"]
    assert set(codes.keys()) == {"0", "1", "2", "3"}
    assert "all pass" in codes["0"]
    assert "FAIL" in codes["1"]
    assert "usage" in codes["2"] or "config" in codes["2"]
    assert "model code" in codes["3"] or "build" in codes["3"]


def test_describe_model_part_and_pose_fields():
    doc = api.describe()
    model_info = doc["model"]
    assert "Part" in model_info and "Pose" in model_info
    part_fields = {f["name"] for f in model_info["Part"]}
    pose_fields = {f["name"] for f in model_info["Pose"]}
    # Part fields from model.py
    assert part_fields >= {"name", "shape", "use", "print", "qty", "material", "color", "printed"}
    # Pose fields from model.py
    assert pose_fields >= {"x", "y", "z", "rx", "ry", "rz"}
    # Every field has type and default (or None for required)
    for fields in (model_info["Part"], model_info["Pose"]):
        for f in fields:
            assert f.get("type")
            assert "default" in f


def test_describe_context_methods():
    doc = api.describe()
    ctx_methods = {m["name"] for m in doc["context"]}
    expected = {"part", "solid", "interference", "distance", "section", "slide", "play", "expect", "record"}
    assert ctx_methods >= expected, f"missing: {expected - ctx_methods}"
    for m in doc["context"]:
        assert m["signature"].startswith(m["name"] + "(")
        assert m["doc"]


def test_describe_config_sections_and_keys():
    doc = api.describe()
    config = doc["config"]
    # Should have all sections from ALLOWED_KEYS
    assert set(config.keys()) == set(project.ALLOWED_KEYS.keys())
    for section, keys in project.ALLOWED_KEYS.items():
        assert set(config[section].keys()) == keys
        # Each key has a default value (or null)
        for key in keys:
            assert key in config[section]


def test_describe_gates_match_builtin_ids():
    doc = api.describe()
    gate_ids = {g["id"] for g in doc["gates"]}
    assert gate_ids == set(checks.BUILTIN_IDS), f"mismatch: gates {gate_ids - set(checks.BUILTIN_IDS)}, builtin {set(checks.BUILTIN_IDS) - gate_ids}"
    # Every gate has a non-empty "what" (guards gate docs)
    for gate in doc["gates"]:
        assert gate["what"], f"gate {gate['id']} has empty 'what': this test guards gate docs"


def test_describe_printers():
    doc = api.describe()
    printers_list = doc["printers"]
    assert len(printers_list) == len(printers.PROFILES)
    for p in printers_list:
        assert "name" in p and "bed_mm" in p and "nozzle_mm" in p
        assert len(p["bed_mm"]) == 3


def test_describe_templates():
    doc = api.describe()
    assert doc["templates"] == list(cli.TEMPLATES)


def test_describe_frames():
    doc = api.describe()
    frames = doc["frames"]
    assert set(frames.keys()) == {"model", "use", "print"}
    for desc in frames.values():
        assert isinstance(desc, str) and len(desc) > 10


def test_describe_report_schema():
    doc = api.describe()
    report_info = doc["report"]
    assert report_info["schema"] == report.SCHEMA
    assert set(report_info["statuses"]) == {s.value for s in report.Status}
    assert "hint" in report_info["check_fields"]
    assert set(report_info["views"]) == {"full", "brief"}


def test_cheat_sheet_under_10kb_and_contains_all():
    doc = api.describe()
    sheet = api.cheat_sheet(doc)
    assert len(sheet.encode("utf-8")) < 10240, "cheat sheet exceeds 10 KB"
    # Names every subcommand
    for cmd in doc["commands"]:
        assert f"`agentcad {cmd['name']}" in sheet  # usage line starts with the command name
    # Names every context method
    for m in doc["context"]:
        assert m["name"] in sheet
    # Names every config key
    for keys in doc["config"].values():
        for key in keys:
            assert key in sheet
    # Names every gate
    for gate in doc["gates"]:
        assert gate["id"] in sheet


def test_cli_json_and_text(capsys):
    # Test --json
    class ArgsJson:
        json = True
    assert api.cli(ArgsJson()) == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "agentcad.api/1"

    # Test plain text
    class ArgsText:
        json = False
    assert api.cli(ArgsText()) == 0
    out = capsys.readouterr().out
    assert "agentcad API cheat sheet" in out
    assert "Exit codes" in out
    assert "Commands" in out


def test_agentcad_api_subprocess_json():
    result = subprocess.run(["agentcad", "api", "--json"],
                            check=False, capture_output=True, text=True, cwd="/Users/aryavora/Desktop/Personal Projects/agentcad")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    data = json.loads(result.stdout)
    assert data["schema"] == "agentcad.api/1"
    assert "commands" in data


def test_agentcad_api_subprocess_text():
    result = subprocess.run(["agentcad", "api"],
                            check=False, capture_output=True, text=True, cwd="/Users/aryavora/Desktop/Personal Projects/agentcad")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout
    assert "agentcad API cheat sheet" in out
    assert "Exit codes" in out