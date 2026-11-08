"""Install, show or locate the agentcad agent skill for Claude Code, Codex, Cursor or AGENTS.md."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from .project import ConfigError

SCHEMA = "agentcad.skill/1"
BEGIN = "<!-- agentcad:begin -->"
END = "<!-- agentcad:end -->"
CURSOR_REFS = "agentcad-references"

# Condensed guide for hosts with no skill folder. Every `agentcad <command>` named here is checked against the
# real parser in the tests, so it cannot drift to commands that do not exist.
AGENTS_BLOCK = f"""{BEGIN}
## agentcad: CAD you can prove

Use agentcad (CadQuery plus measurement) for any task that designs, changes or checks a 3D part or print.

Core rule: never report a dimension, clearance or PASS that you did not measure from the built geometry. Parameters
in `model.py` are intent, not evidence.

The loop:

1. `agentcad new NAME --template blank` (or `bracket`, `box-with-lid`), then edit `NAME/model.py` and
   `NAME/agentcad.toml`. Write a check for every requirement in the brief.
2. `agentcad check NAME --json --brief` builds the model and measures it. Read every FAIL: `summary`, `measured`,
   `limit`, `where`, `hint`.
3. Fix the geometry at the place `where` names. Do not loosen a limit unless the requirement itself changed.
4. `agentcad render NAME`, then open `NAME/build/renders/sheet.png` and look at it. Renders catch gross errors such
   as a missing feature; they never prove thickness, clearance or overlap.
5. Run `agentcad check NAME` again after the last edit.

Exit codes: 0 pass or warn, 1 a gate failed, 2 usage or config error, 3 the model code failed to build.

Names and signatures: `agentcad api` prints every command, ctx helper, config key and gate in one document
(`agentcad api --json` for machines). Do not guess them.

Probe solids stand in for the mating thing (an M4 screw is a 4.0 mm cylinder). Build them from the requirement,
never from the model's own parameters, or the check can never fail.

Full guide: `agentcad skill show`. One reference: `agentcad skill show --reference design-for-printing`
(also `cadquery-patterns` and `verification-playbook`).
{END}
"""

HOSTS = ("claude", "codex", "cursor", "agents-md")


def _usage_error(message: str) -> Exception:
    # Imported here: cli imports this module lazily, so a top-level import would be circular.
    from .cli import UsageError

    return UsageError(message)


def skill_dir() -> Path:
    """The folder holding SKILL.md: the copy packed in the wheel, else the source tree's."""
    packaged = Path(__file__).parent / "_skill"
    if (packaged / "SKILL.md").is_file():
        return packaged
    source = Path(__file__).resolve().parents[2] / "skills" / "agentcad"
    if (source / "SKILL.md").is_file():
        return source
    raise ConfigError("the agentcad skill files are missing from this install; "
                      "reinstall with: uv tool install --reinstall agentcad")


def _reference_names() -> list[str]:
    refs = skill_dir() / "references"
    return sorted(p.stem for p in refs.glob("*.md")) if refs.is_dir() else []


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def _split_skill(text: str) -> tuple[str, str]:
    """Return (description, body) from SKILL.md frontmatter."""
    match = re.match(r"---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    if not match:
        return "", text
    desc = re.search(r"^description:\s*(.+)$", match.group(1), re.MULTILINE)
    return (desc.group(1).strip() if desc else ""), match.group(2).lstrip("\n")


# -- planning: each host yields {absolute path: bytes} for copies, or a link target ----------------------------------

def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _project_dir(args: argparse.Namespace) -> Path:
    return Path(args.project) if args.project else Path.cwd()


def _cursor_files() -> dict[str, bytes]:
    description, body = _split_skill((skill_dir() / "SKILL.md").read_text(encoding="utf-8"))
    body = re.sub(r"(?<![\w/.-])references/", f".cursor/rules/{CURSOR_REFS}/", body)
    # JSON strings are valid YAML double-quoted scalars, which keeps colons and quotes in the description safe.
    front = (f"---\ndescription: {json.dumps(description)}\nglobs: \"**/agentcad.toml,**/model.py\"\n"
             "alwaysApply: false\n---\n\n")
    out = {".cursor/rules/agentcad.mdc": (front + body).encode("utf-8")}
    refs = skill_dir() / "references"
    for name, data in (_files(refs) if refs.is_dir() else {}).items():
        out[f".cursor/rules/{CURSOR_REFS}/{name}"] = data
    return out


def _result(args: argparse.Namespace, action: str, dest: Path, status: str, files: list[str]) -> int:
    if args.json:
        print(json.dumps({"schema": SCHEMA, "action": action, "host": args.host, "dest": str(dest),
                          "status": status, "files": files}, indent=2))
    elif status == "unchanged":
        print(f"already up to date: {dest}")
    else:
        print(f"{status} agentcad skill for {args.host}: {dest}")
    return 0


def _differs(dest: Path) -> Exception:
    return _usage_error(f"exists and differs: {dest}; re-run with --force to replace")


def _install_folder(args: argparse.Namespace, dest: Path) -> int:
    src = skill_dir()
    wanted = _files(src)
    existing = dest.is_symlink() or dest.exists()
    if existing:
        if args.link:
            same = dest.is_symlink() and dest.resolve() == src.resolve()
        else:
            same = dest.is_dir() and not dest.is_symlink() and _files(dest) == wanted
        if same:
            return _result(args, "install", dest, "unchanged", sorted(wanted))
        if not args.force:
            raise _differs(dest)
        if dest.is_symlink():
            dest.unlink()
        elif dest.is_dir() and (dest / "SKILL.md").is_file():
            shutil.rmtree(dest)
        else:
            raise _usage_error(f"refusing to replace {dest}: it has no SKILL.md, so it is not an agentcad skill; move it away first")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if args.link:
        dest.symlink_to(src.resolve(), target_is_directory=True)
    else:
        for rel, data in wanted.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return _result(args, "install", dest, "updated" if existing else "installed", sorted(wanted))


def _install_cursor(args: argparse.Namespace, root: Path) -> int:
    wanted = _cursor_files()
    rels = sorted(wanted)
    state = [(root / rel).is_file() for rel in rels]
    # Report the rule file itself, not whichever reference sorts first.
    rule = root / ".cursor/rules/agentcad.mdc"
    if all(state) and all((root / rel).read_bytes() == wanted[rel] for rel in rels):
        return _result(args, "install", rule, "unchanged", rels)
    clash = [root / rel for rel in rels if (root / rel).exists() and not (root / rel).is_file()]
    if clash:
        raise _usage_error(f"refusing to replace {clash[0]}: it is not a file; move it away first")
    mismatch = any((root / rel).exists() and (root / rel).read_bytes() != wanted[rel] for rel in rels)
    if mismatch and not args.force:
        raise _differs(next(root / rel for rel in rels
                            if (root / rel).exists() and (root / rel).read_bytes() != wanted[rel]))
    for rel in rels:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(wanted[rel])
    return _result(args, "install", rule, "updated" if any(state) else "installed", rels)


def _install_agents_md(args: argparse.Namespace, root: Path) -> int:
    dest = root / "AGENTS.md"
    block = AGENTS_BLOCK
    if not dest.exists():
        root.mkdir(parents=True, exist_ok=True)
        dest.write_text(block, encoding="utf-8")
        return _result(args, "install", dest, "installed", ["AGENTS.md"])
    if not dest.is_file():
        raise _usage_error(f"refusing to replace {dest}: it is not a file")
    # Read as bytes so CRLF and odd encodings outside the block survive untouched.
    text = dest.read_bytes().decode("utf-8")
    begin, end = text.find(BEGIN), text.find(END)
    if (begin == -1) != (end == -1) or (begin != -1 and end < begin):
        raise _usage_error(f"{dest} has a broken agentcad block (needs {BEGIN} before {END}); fix or remove the markers")
    if begin == -1:
        sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
        new, status = text + sep + block, "installed"
    else:
        stop = end + len(END)
        if text[stop:stop + 1] == "\n":
            stop += 1
        new, status = text[:begin] + block + text[stop:], "updated"
        if new == text:
            return _result(args, "install", dest, "unchanged", ["AGENTS.md"])
        if not args.force:
            raise _differs(dest)
    dest.write_bytes(new.encode("utf-8"))
    return _result(args, "install", dest, status, ["AGENTS.md"])


def _install(args: argparse.Namespace) -> int:
    host = args.host
    if host == "claude":
        base = Path(args.project) / ".claude" if args.project else _home() / ".claude"
        return _install_folder(args, base / "skills" / "agentcad")
    if host == "codex":
        if args.link:
            raise _usage_error("codex skips symlinked skills; install without --link")
        if args.project:
            raise _usage_error("codex skills are user-wide; for a project use: agentcad skill install --host agents-md --project DIR")
        codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else _home() / ".codex"
        return _install_folder(args, codex_home / "skills" / "agentcad")
    if args.link:
        raise _usage_error(f"--link only works with --host claude; {host} gets a copy")
    if host == "cursor":
        return _install_cursor(args, _project_dir(args))
    return _install_agents_md(args, _project_dir(args))


def _show(args: argparse.Namespace) -> int:
    root = skill_dir()
    if args.reference:
        names = _reference_names()
        name = args.reference.removesuffix(".md")
        if name not in names:
            raise _usage_error(f"unknown reference '{args.reference}'; choose one of: {', '.join(names)}")
        path = root / "references" / f"{name}.md"
    else:
        path = root / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    if args.json:
        print(json.dumps({"schema": SCHEMA, "action": "show", "path": str(path), "content": text}, indent=2))
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad skill``."""
    if args.action == "path":
        path = skill_dir()
        if args.json:
            print(json.dumps({"schema": SCHEMA, "action": "path", "path": str(path)}, indent=2))
        else:
            print(path)
        return 0
    if args.action == "show":
        return _show(args)
    return _install(args)
