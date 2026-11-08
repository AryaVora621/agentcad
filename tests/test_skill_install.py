"""agentcad skill: install, show and path, always against a sandboxed HOME."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentcad import cli, skill_install


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path


def snapshot(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def run(capsys, *argv):
    code = cli.main(["skill", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_skill_dir_has_skill_and_references():
    root = skill_install.skill_dir()
    assert (root / "SKILL.md").is_file()
    assert (root / "references").is_dir()


def test_path(capsys, sandbox):
    code, out, _ = run(capsys, "path")
    assert code == 0 and out.strip() == str(skill_install.skill_dir())
    code, out, _ = run(capsys, "path", "--json")
    assert json.loads(out)["path"] == str(skill_install.skill_dir())


def test_show_skill_and_references(capsys, sandbox):
    code, out, _ = run(capsys, "show")
    assert code == 0 and out.startswith("---\nname: agentcad")
    for name in ("design-for-printing", "design-for-printing.md"):
        code, out, _ = run(capsys, "show", "--reference", name)
        assert code == 0 and out.startswith("# ")


def test_show_unknown_reference(capsys, sandbox):
    code, _, err = run(capsys, "show", "--reference", "nope")
    assert code == 2
    assert "cadquery-patterns" in err and "verification-playbook" in err and "Traceback" not in err
    code, out, _ = run(capsys, "show", "--reference", "../SKILL", "--json")
    assert code == 2 and json.loads(out)["schema"] == "agentcad.error/1"


def test_claude_user_scope(capsys, sandbox):
    before = snapshot(sandbox)
    code, out, _ = run(capsys, "install", "--json")
    assert code == 0
    doc = json.loads(out)
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    assert doc["schema"] == "agentcad.skill/1" and doc["status"] == "installed" and doc["host"] == "claude"
    assert doc["dest"] == str(dest) and "SKILL.md" in doc["files"] and "references/cadquery-patterns.md" in doc["files"]
    assert (dest / "SKILL.md").read_bytes() == (skill_install.skill_dir() / "SKILL.md").read_bytes()
    assert not dest.is_symlink()
    new = snapshot(sandbox) - before
    assert all(n.startswith("home/.claude") for n in new)


def test_claude_project_scope(capsys, sandbox):
    proj = sandbox / "proj"
    code, out, _ = run(capsys, "install", "--project", str(proj))
    assert code == 0 and "installed" in out
    assert (proj / ".claude" / "skills" / "agentcad" / "SKILL.md").is_file()
    assert not (sandbox / "home" / ".claude").exists()


def test_unchanged_writes_nothing(capsys, sandbox):
    run(capsys, "install")
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    mtimes = {p: p.stat().st_mtime_ns for p in dest.rglob("*")}
    code, out, _ = run(capsys, "install")
    assert code == 0 and "already up to date" in out
    code, out, _ = run(capsys, "install", "--json")
    assert json.loads(out)["status"] == "unchanged"
    assert {p: p.stat().st_mtime_ns for p in dest.rglob("*")} == mtimes


def test_differs_needs_force(capsys, sandbox):
    run(capsys, "install")
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    (dest / "SKILL.md").write_text("edited")
    code, _, err = run(capsys, "install")
    assert code == 2 and "exists and differs" in err and "--force" in err
    assert (dest / "SKILL.md").read_text() == "edited"
    code, out, _ = run(capsys, "install", "--force", "--json")
    assert code == 0 and json.loads(out)["status"] == "updated"
    assert (dest / "SKILL.md").read_bytes() == (skill_install.skill_dir() / "SKILL.md").read_bytes()


def test_force_refuses_folder_without_skill_md(capsys, sandbox):
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    dest.mkdir(parents=True)
    (dest / "precious.txt").write_text("keep me")
    code, _, err = run(capsys, "install", "--force")
    assert code == 2 and "SKILL.md" in err
    assert (dest / "precious.txt").read_text() == "keep me"


def test_force_refuses_a_plain_file(capsys, sandbox):
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    dest.parent.mkdir(parents=True)
    dest.write_text("a file")
    code, _, _ = run(capsys, "install", "--force")
    assert code == 2 and dest.read_text() == "a file"


def test_link(capsys, sandbox):
    code, out, _ = run(capsys, "install", "--link", "--json")
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    assert code == 0 and json.loads(out)["status"] == "installed"
    assert dest.is_symlink() and dest.resolve() == skill_install.skill_dir().resolve()
    code, out, _ = run(capsys, "install", "--link")
    assert code == 0 and "already up to date" in out
    # A copy over a link is a different install and must be asked for.
    code, _, err = run(capsys, "install")
    assert code == 2 and "differs" in err
    code, _, _ = run(capsys, "install", "--force")
    assert code == 0 and not dest.is_symlink() and (dest / "SKILL.md").is_file()
    # Forcing a link over the copy removes only the copy, never the packaged skill.
    code, _, _ = run(capsys, "install", "--link", "--force")
    assert code == 0 and dest.is_symlink() and (skill_install.skill_dir() / "SKILL.md").is_file()


def test_force_replaces_symlink_without_touching_target(capsys, sandbox):
    target = sandbox / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    dest = sandbox / "home" / ".claude" / "skills" / "agentcad"
    dest.parent.mkdir(parents=True)
    dest.symlink_to(target, target_is_directory=True)
    code, _, _ = run(capsys, "install", "--force")
    assert code == 0 and not dest.is_symlink()
    assert (target / "keep.txt").read_text() == "x" and not (target / "SKILL.md").exists()


def test_codex(capsys, sandbox):
    code, out, _ = run(capsys, "install", "--host", "codex", "--json")
    assert code == 0
    assert json.loads(out)["dest"] == str(sandbox / "codex" / "skills" / "agentcad")
    assert (sandbox / "codex" / "skills" / "agentcad" / "SKILL.md").is_file()
    assert not (sandbox / "home" / ".codex").exists()


def test_codex_default_home(capsys, sandbox, monkeypatch):
    monkeypatch.delenv("CODEX_HOME")
    code, _, _ = run(capsys, "install", "--host", "codex")
    assert code == 0 and (sandbox / "home" / ".codex" / "skills" / "agentcad" / "SKILL.md").is_file()


def test_codex_rejects_link_and_project(capsys, sandbox):
    code, _, err = run(capsys, "install", "--host", "codex", "--link")
    assert code == 2 and "symlink" in err
    code, _, err = run(capsys, "install", "--host", "codex", "--project", str(sandbox / "p"))
    assert code == 2 and "--host agents-md --project" in err
    assert not (sandbox / "codex").exists() and not (sandbox / "p").exists()


def test_cursor(capsys, sandbox):
    proj = sandbox / "proj"
    code, out, _ = run(capsys, "install", "--host", "cursor", "--project", str(proj), "--json")
    assert code == 0 and json.loads(out)["status"] == "installed"
    mdc = (proj / ".cursor" / "rules" / "agentcad.mdc").read_text()
    front, body = mdc.split("\n---\n", 1)
    assert front.startswith("---\ndescription: ") and 'globs: "**/agentcad.toml,**/model.py"' in front
    assert "alwaysApply: false" in front
    description = json.loads(re.search(r"description: (.+)", front).group(1))
    assert description.startswith("Design, check and export 3D parts")
    assert "(.cursor/rules/agentcad-references/design-for-printing.md)" in body
    assert not re.search(r"(?<![\w/.-])references/", body)
    for name in ("design-for-printing", "cadquery-patterns", "verification-playbook"):
        assert (proj / ".cursor" / "rules" / "agentcad-references" / f"{name}.md").is_file()
    code, out, _ = run(capsys, "install", "--host", "cursor", "--project", str(proj))
    assert code == 0 and "already up to date" in out


def test_cursor_defaults_to_cwd_and_guards(capsys, sandbox):
    code, _, _ = run(capsys, "install", "--host", "cursor")
    assert code == 0 and (sandbox / "work" / ".cursor" / "rules" / "agentcad.mdc").is_file()
    mdc = sandbox / "work" / ".cursor" / "rules" / "agentcad.mdc"
    mdc.write_text("mine")
    code, _, err = run(capsys, "install", "--host", "cursor")
    assert code == 2 and "differs" in err and mdc.read_text() == "mine"
    code, _, _ = run(capsys, "install", "--host", "cursor", "--force")
    assert code == 0 and mdc.read_text() != "mine"
    code, _, err = run(capsys, "install", "--host", "cursor", "--link")
    assert code == 2


def test_agents_md_created(capsys, sandbox):
    proj = sandbox / "proj"
    code, out, _ = run(capsys, "install", "--host", "agents-md", "--project", str(proj), "--json")
    assert code == 0 and json.loads(out)["files"] == ["AGENTS.md"]
    text = (proj / "AGENTS.md").read_text()
    assert text.startswith(skill_install.BEGIN) and text.rstrip().endswith(skill_install.END)
    assert 30 <= len(skill_install.AGENTS_BLOCK.strip().splitlines()) <= 45
    code, out, _ = run(capsys, "install", "--host", "agents-md", "--project", str(proj))
    assert code == 0 and "already up to date" in out


def test_agents_md_preserves_text_around_block(capsys, sandbox):
    proj = sandbox / "proj"
    proj.mkdir()
    before, after = "# My rules\r\nbe nice\n\n", "\n## After\nkeep é this  \n"
    stale = f"{before}{skill_install.BEGIN}\nold block\n{skill_install.END}\n{after}"
    (proj / "AGENTS.md").write_bytes(stale.encode())
    code, _, err = run(capsys, "install", "--host", "agents-md", "--project", str(proj))
    assert code == 2 and "differs" in err and (proj / "AGENTS.md").read_bytes() == stale.encode()
    code, out, _ = run(capsys, "install", "--host", "agents-md", "--project", str(proj), "--force", "--json")
    assert code == 0 and json.loads(out)["status"] == "updated"
    first = (proj / "AGENTS.md").read_bytes()
    text = first.decode()
    assert text.startswith(before) and text.endswith(after) and "old block" not in text
    code, out, _ = run(capsys, "install", "--host", "agents-md", "--project", str(proj))
    assert code == 0 and "already up to date" in out and (proj / "AGENTS.md").read_bytes() == first


def test_agents_md_appends_to_existing_file(capsys, sandbox):
    proj = sandbox / "proj"
    proj.mkdir()
    (proj / "AGENTS.md").write_text("# Mine\nno trailing blank")
    code, out, _ = run(capsys, "install", "--host", "agents-md", "--project", str(proj), "--json")
    assert code == 0 and json.loads(out)["status"] == "installed"
    text = (proj / "AGENTS.md").read_text()
    assert text.startswith("# Mine\nno trailing blank\n\n" + skill_install.BEGIN)


def test_agents_md_broken_markers(capsys, sandbox):
    proj = sandbox / "proj"
    proj.mkdir()
    (proj / "AGENTS.md").write_text(f"x\n{skill_install.BEGIN}\nno end\n")
    code, _, err = run(capsys, "install", "--host", "agents-md", "--project", str(proj), "--force")
    assert code == 2 and "broken agentcad block" in err


def test_block_names_only_real_commands():
    commands = set(cli._parser()._subparsers._group_actions[0].choices)
    named = set(re.findall(r"`agentcad ([a-z][a-z-]*)", skill_install.AGENTS_BLOCK))
    assert {"new", "check", "render", "api", "skill"} <= named
    assert named <= commands, named - commands


def test_missing_skill_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_install, "__file__", str(tmp_path / "pkg" / "skill_install.py"))
    with pytest.raises(skill_install.ConfigError, match="reinstall"):
        skill_install.skill_dir()


def test_nothing_written_outside_sandbox(capsys, sandbox):
    # The real home holds a live symlink to this repo; any stray write would corrupt it.
    real = Path(__file__).resolve().parent
    run(capsys, "install", "--link")
    run(capsys, "install", "--host", "codex")
    run(capsys, "install", "--host", "cursor", "--project", str(sandbox / "c"))
    run(capsys, "install", "--host", "agents-md", "--project", str(sandbox / "a"))
    assert not (real / ".claude").exists() and not (real / ".cursor").exists() and not (real / "AGENTS.md").exists()
    top = {p.name for p in sandbox.iterdir()}
    assert top <= {"home", "codex", "work", "c", "a"}
