"""MCP server: agentcad's build, check, measure, render and drawing commands as tools.

Placeholder until this feature lands.
"""
from __future__ import annotations

import argparse
import sys


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad mcp``."""
    print("agentcad: error: 'mcp' is not implemented yet", file=sys.stderr)
    return 2
