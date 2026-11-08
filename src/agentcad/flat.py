"""2D flat parts cut on a laser, CNC router or waterjet: profile export and the flat gates.

Placeholder until the flat-parts work lands: no gate ids and no checks.
"""
from __future__ import annotations

FLAT_IDS: tuple[str, ...] = ()


def flat_gates(ctx, wanted: set[str] | None) -> list:
    """Return the flat gates' checks for every cut part (none yet)."""
    return []
