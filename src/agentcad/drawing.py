"""Dimensioned 2D drawings (SVG, PDF, PNG) taken from the measured geometry.

Placeholder until this feature lands.
"""
from __future__ import annotations

import argparse
import sys


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad drawing``."""
    print("agentcad: error: 'drawing' is not implemented yet", file=sys.stderr)
    return 2
