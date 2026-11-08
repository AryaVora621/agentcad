"""Install, show or locate the agentcad agent skill for Claude Code, Codex, Cursor or AGENTS.md.

Placeholder until this feature lands.
"""
from __future__ import annotations

import argparse
import sys


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad skill``."""
    print("agentcad: error: 'skill' is not implemented yet", file=sys.stderr)
    return 2
