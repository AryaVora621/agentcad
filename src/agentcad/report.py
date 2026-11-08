"""Check results and their JSON and table renderings (schema ``agentcad.report/1``)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

SCHEMA = "agentcad.report/1"


class Status(str, Enum):
    """Outcome of one check."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"


def json_value(value: object) -> object:
    """Convert numpy types and enums to plain JSON values; round floats to 4 decimals, NaN and inf to null."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float):
        # Adding 0.0 turns -0.0 into 0.0 so tiny negative noise does not print as "-0.0".
        return round(value, 4) + 0.0 if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


@dataclass
class Check:
    """One measured assertion: what was measured, the limit, the verdict and where to look."""

    id: str
    part: str | None
    status: Status
    summary: str
    measured: dict = field(default_factory=dict)
    limit: dict = field(default_factory=dict)
    where: list[dict] = field(default_factory=list)
    # How to fix it, for FAIL and WARN; omitted from JSON when None so older readers see the same keys.
    hint: str | None = None

    def to_dict(self) -> dict:
        """Return the JSON-ready form."""
        document = {"id": self.id, "part": self.part, "status": self.status, "summary": self.summary,
                    "measured": self.measured, "limit": self.limit, "where": self.where}
        if self.hint is not None:
            document["hint"] = self.hint
        return json_value(document)


@dataclass
class Report:
    """A list of checks from one command run."""

    tool: str
    project: str | None = None
    checks: list[Check] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def status(self) -> Status:
        """FAIL if any check FAILs or ERRORs, else WARN if any WARNs, else PASS."""
        if any(c.status in (Status.FAIL, Status.ERROR) for c in self.checks):
            return Status.FAIL
        return Status.WARN if any(c.status is Status.WARN for c in self.checks) else Status.PASS

    def exit_code(self) -> int:
        """0 for PASS or WARN, 1 when any check FAILs, 3 when only ERRORs remain (a build failure)."""
        if any(c.status is Status.FAIL for c in self.checks):
            return 1
        return 3 if any(c.status is Status.ERROR for c in self.checks) else 0

    def counts(self) -> dict[str, int]:
        """Number of checks per status."""
        return {s.value.lower(): sum(c.status is s for c in self.checks) for s in Status}

    def to_json(self, view: str = "full") -> str:
        """Serialise as one JSON document; view "brief" is the token-lean form for agents."""
        if view == "brief":
            raise NotImplementedError("brief view lands with the lean-output work")
        if view != "full":
            raise ValueError(f"unknown report view {view!r}; use 'full' or 'brief'")
        document = {"schema": SCHEMA, "tool": self.tool, "project": self.project, "status": self.status(),
                    "exit_code": self.exit_code(), "counts": self.counts(),
                    "checks": [c.to_dict() for c in self.checks], "meta": self.meta}
        return json.dumps(json_value(document), indent=2, allow_nan=False)

    def to_brief_table(self) -> str:
        """Only the checks that need attention, with hints, then the totals line."""
        raise NotImplementedError("brief table lands with the lean-output work")

    def to_table(self) -> str:
        """One line per check, then a totals line."""
        width = max([len(c.id) for c in self.checks] + [2])
        part_width = max([len(c.part or "-") for c in self.checks] + [4])
        lines = [f"{c.status.value:<5}  {c.id:<{width}}  {c.part or '-':<{part_width}}  {c.summary}" for c in self.checks]
        n = self.counts()
        total = f"{n['pass']} pass, {n['warn']} warn, {n['fail']} fail"
        if n["error"]:
            total += f", {n['error']} error"
        if n["skip"]:
            total += f", {n['skip']} skip"
        return "\n".join([*lines, total])
