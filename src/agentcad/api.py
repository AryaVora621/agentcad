"""Machine-readable description of agentcad for agents: commands, check helpers, config keys, gates.

Placeholder until this feature lands.
"""
from __future__ import annotations

import argparse
import sys


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad api``."""
    print("agentcad: error: 'api' is not implemented yet", file=sys.stderr)
    return 2
