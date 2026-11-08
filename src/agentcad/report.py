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
            return self._brief_json()
        if view != "full":
            raise ValueError(f"unknown report view {view!r}; use 'full' or 'brief'")
        document = {"schema": SCHEMA, "tool": self.tool, "project": self.project, "status": self.status(),
                    "exit_code": self.exit_code(), "counts": self.counts(),
                    "checks": [c.to_dict() for c in self.checks], "meta": self.meta}
        return json.dumps(json_value(document), indent=2, allow_nan=False)

    def _brief_json(self) -> str:
        """Non-PASS checks in full, PASS checks folded into ``passed`` by id, compact separators.

        Agents re-read this after every edit, so the passing rows (most of a healthy report) cost one short
        name each instead of a whole object.
        """
        attention = []
        for c in self.checks:
            if c.status is Status.PASS:
                continue
            entry = c.to_dict()
            for key in ("measured", "limit", "where"):
                if not entry[key]:
                    del entry[key]
            attention.append(entry)
        # Imported here because checks imports this module. Project checks are the requirement checks an agent has
        # to quote, so their passes keep their summary; built-in passes fold into ids.
        from .checks import BUILTIN_IDS

        passed: dict[str, list[str]] = {}
        passed_checks: list[dict] = []
        for c in self.checks:
            if c.status is not Status.PASS:
                continue
            if self.tool == "check" and c.id not in BUILTIN_IDS:
                passed_checks.append({"id": c.id, "part": c.part, "summary": c.summary})
            else:
                passed.setdefault(c.id, []).append(c.part or "-")
        meta = {k: self.meta[k] for k in ("elapsed_s", "build_s", "build") if k in self.meta}
        document = {"schema": SCHEMA, "view": "brief", "tool": self.tool, "project": self.project,
                    "status": self.status(), "exit_code": self.exit_code(), "counts": self.counts(),
                    "checks": attention, "passed": passed, "passed_checks": passed_checks, "meta": meta}
        return json.dumps(json_value(document), separators=(",", ":"), allow_nan=False)

    def to_brief_table(self) -> str:
        """Only the checks that need attention, with hints, then a passed summary and the totals line."""
        attention = [c for c in self.checks if c.status is not Status.PASS]
        width = max([len(c.id) for c in attention] + [2])
        part_width = max([len(c.part or "-") for c in attention] + [4])
        lines: list[str] = []
        for c in attention:
            lines.append(f"{c.status.value:<5}  {c.id:<{width}}  {c.part or '-':<{part_width}}  {c.summary}")
            if c.hint:
                lines.append(f"  hint: {c.hint}")
        counts: dict[str, int] = {}
        for c in self.checks:
            if c.status is Status.PASS:
                counts[c.id] = counts.get(c.id, 0) + 1
        if counts:
            lines.append(f"{sum(counts.values())} passed: " + ", ".join(f"{k} x{v}" for k, v in counts.items()))
        lines.append(self.to_table().splitlines()[-1])
        return "\n".join(lines)

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
        # Both times, so a reader can tell the model was rebuilt (build_s) from how long the gates took (elapsed_s).
        if "build_s" in self.meta and "elapsed_s" in self.meta:
            total += f" (build {self.meta['build_s']:g} s, checks {self.meta['elapsed_s']:g} s)"
        return "\n".join([*lines, total])
