"""Shared fixtures: projects copied to temp folders, a CLI runner and mesh helpers."""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from helpers import check_project

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def copy_example(tmp_path):
    """Copy an example project into tmp_path so builds never touch the repository."""
    def copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(EXAMPLES / name, target, ignore=shutil.ignore_patterns("build", "__pycache__"))
        return target
    return copy


@pytest.fixture
def make_project(tmp_path):
    """Write a throwaway project: ``make_project(model_source, toml_extra="")`` returns its folder."""
    counter = iter(range(1000))

    def make(model: str, toml: str = "", files: dict[str, str] | None = None) -> Path:
        folder = tmp_path / f"proj{next(counter)}"
        folder.mkdir()
        (folder / "agentcad.toml").write_text('[project]\nname = "t"\nmodule = "model.py"\n' + textwrap.dedent(toml))
        (folder / "model.py").write_text(textwrap.dedent(model))
        for name, source in (files or {}).items():
            (folder / name).write_text(textwrap.dedent(source))
        return folder
    return make


@pytest.fixture(scope="session")
def broken_report(tmp_path_factory):
    """The examples/broken report, computed once for the whole session."""
    folder = tmp_path_factory.mktemp("broken") / "broken"
    shutil.copytree(EXAMPLES / "broken", folder, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return check_project(folder)


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    """Run ``agentcad`` in this process: ``cli("check", folder, "--json")`` -> (exit code, stdout, stderr).

    In-process runs keep the suite fast (importing CadQuery costs seconds per subprocess); ``cli_process``
    covers the real console entry point.
    """
    from agentcad.cli import main

    def run(*args, cwd: Path | None = None):
        monkeypatch.chdir(cwd or tmp_path)
        try:
            code = main([str(a) for a in args])
        except SystemExit as exit_:  # argparse exits with 2 on usage errors
            code = exit_.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return run


@pytest.fixture
def cli_process(tmp_path):
    """Run ``python -m agentcad.cli`` as a subprocess, as an agent would: -> (exit code, stdout, stderr)."""
    def run(*args, cwd: Path | None = None):
        proc = subprocess.run([sys.executable, "-m", "agentcad.cli", *map(str, args)], capture_output=True,
                              text=True, cwd=cwd or tmp_path, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    return run
