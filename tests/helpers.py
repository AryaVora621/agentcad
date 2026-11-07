"""Helpers shared by the tests."""
from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq
import trimesh

from agentcad.build import build
from agentcad.checks import run_checks
from agentcad.geometry import to_mesh, to_shape
from agentcad.project import Project


def check_project(folder: Path, only: list[str] | None = None):
    """Build and check a project in-process; returns the Report."""
    project = Project.load(folder)
    return run_checks(project, build(project), only)


def parse(stdout: str) -> dict:
    return json.loads(stdout)


def print_mesh(shape: cq.Workplane | cq.Shape, tolerance: float = 0.02) -> trimesh.Trimesh:
    """Tessellate a shape that is already in the print frame."""
    return to_mesh(to_shape(shape), tolerance)


def by_id(report, id: str, part: str | None = None):
    """Checks of a report with the given id (and part)."""
    return [c for c in report.checks if c.id == id and (part is None or c.part == part)]
