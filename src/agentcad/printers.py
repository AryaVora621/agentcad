"""Generic printer envelopes and optional local profiles."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Printer:
    """Usable build envelope and nominal nozzle diameter, in millimetres."""

    name: str
    bed_xyz: tuple[float, float, float]
    nozzle_mm: float = 0.4
    notes: str = "Generic build envelope; verify your machine's usable area."


PROFILES = {f"generic-{size}": Printer(f"generic-{size}", (size, size, height))
            for size, height in [(220, 250), (256, 256), (180, 180), (300, 300), (350, 340)]}


def load_user_profiles(path: str | Path | None = None) -> dict[str, Printer]:
    """Read [printers.NAME] entries from the user's optional TOML file."""
    path = Path(path) if path else Path.home() / ".config/agentcad/printers.toml"
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if set(data) - {"printers"}:
        raise ValueError("Unknown profile section; allowed keys: printers")
    result = {}
    for name, values in data.get("printers", {}).items():
        unknown = set(values) - {"bed", "nozzle_mm", "notes"}
        if unknown:
            raise ValueError(f"Unknown printer key {min(unknown)}; allowed keys: bed, nozzle_mm, notes")
        bed = values.get("bed")
        if not isinstance(bed, list) or len(bed) != 3 or any(not isinstance(x, (int, float)) or x <= 0 for x in bed):
            raise ValueError(f"Printer {name}: bed must contain three positive dimensions")
        result[name] = Printer(name, tuple(bed), values.get("nozzle_mm", 0.4), values.get("notes", "User profile"))
    return result


def get_printer(name: str = "generic-220") -> Printer:
    """Resolve a built-in or user-defined printer by name."""
    profiles = {**PROFILES, **load_user_profiles()}
    if name not in profiles:
        raise ValueError(f"Unknown printer {name!r}; available: {', '.join(sorted(profiles))}")
    return profiles[name]
