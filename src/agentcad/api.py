"""Machine-readable description of agentcad for agents: commands, check helpers, config keys, gates."""
from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import re
from typing import get_type_hints

from . import __version__, checks, flat, model, printers, project, report
from .cli import TEMPLATES, _parser

# Documentation for the 3D printing gates (flat gates add their own via flat.GATE_DOCS)
GATE_DOCS: dict[str, str] = {
    "valid": "the shape is not a valid solid with positive volume",
    "single_solid": "WARN when a part is more than one disconnected solid",
    "watertight": "the print STL is not watertight or has inconsistent winding",
    "bed_fit": "the print-frame bounding box does not fit the printer in either XY orientation",
    "bed_contact": "WARN when little area touches the bed (likely to tip or detach)",
    "overhang": "unsupported downward faces exceed checks.overhang_allow_mm2; faces centred below checks.first_layer_mm are exempt",
    "min_wall": "enabled by checks.min_wall_mm > 0; FAIL when the 1st percentile wall thickness is below it",
    "interference": "use-pose overlap exceeds checks.interference_tol_mm3 unless the pair is in allow_overlap",
}


def describe() -> dict:
    """Return the complete agentcad API description as a dict (library function for MCP)."""
    # Commands from cli._parser() subparsers
    parser = _parser()
    subparsers_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    commands = []
    for action in subparsers_actions:
        for name, subparser in action.choices.items():
            if name in ("-h", "--help"):
                continue
            args_list = []
            for arg_action in subparser._actions:
                if arg_action.dest in ("help", "handler", "command"):
                    continue
                flags = arg_action.option_strings if arg_action.option_strings else [arg_action.dest]
                arg_info = {
                    "flags": flags,
                    "help": arg_action.help or "",
                }
                if arg_action.default is not None and arg_action.default != argparse.SUPPRESS:
                    arg_info["default"] = arg_action.default
                if hasattr(arg_action, "choices") and arg_action.choices:
                    arg_info["choices"] = list(arg_action.choices)
                if arg_action.required:
                    arg_info["required"] = True
                args_list.append(arg_info)
            commands.append({
                "name": name,
                "help": subparser.description or subparser.help or "",
                "args": args_list,
            })

    # Model: Part and Pose fields
    def _dataclass_fields(cls) -> list[dict]:
        hints = get_type_hints(cls)
        fields = []
        for field in dataclasses.fields(cls):
            field_info = {"name": field.name, "type": str(hints.get(field.name, field.type))}
            if field.default is not dataclasses.MISSING:
                field_info["default"] = repr(field.default)
            elif field.default_factory is not dataclasses.MISSING:
                field_info["default"] = "factory"
            else:
                field_info["default"] = None
            fields.append(field_info)
        return fields

    model_info = {
        "Part": _dataclass_fields(model.Part),
        "Pose": _dataclass_fields(model.Pose),
    }

    # Context: public methods of CheckContext
    context_methods = []
    for name, method in inspect.getmembers(checks.CheckContext, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        sig = inspect.signature(method)
        doc = (method.__doc__ or "").strip().split("\n")[0] if method.__doc__ else ""
        context_methods.append({
            "name": name,
            "signature": f"{name}{sig}",
            "doc": doc,
        })

    # Config: from project.ALLOWED_KEYS and DEFAULT_* dicts
    config = {}
    for section, keys in project.ALLOWED_KEYS.items():
        default_dict_name = f"DEFAULT_{section.upper()}"
        defaults = getattr(project, default_dict_name, {})
        config[section] = {key: defaults.get(key) for key in sorted(keys)}

    # Gates: from checks.BUILTIN_IDS with docs
    gates = []
    flat_gate_docs = getattr(flat, "GATE_DOCS", {})
    for gate_id in checks.BUILTIN_IDS:
        what = GATE_DOCS.get(gate_id, flat_gate_docs.get(gate_id, ""))
        gates.append({"id": gate_id, "what": what})

    # Printers
    printers_list = [
        {"name": p.name, "bed_mm": list(p.bed_xyz), "nozzle_mm": p.nozzle_mm}
        for p in printers.PROFILES.values()
    ]

    # Frames (from SKILL.md section 6)
    frames = {
        "model": "the part in its own coordinates",
        "use": "the assembled pose, Part.use",
        "print": "on the bed, z = 0 at the bottom, XY centred, Part.print rotation",
    }

    # Report schema info
    report_info = {
        "schema": report.SCHEMA,
        "statuses": [s.value for s in report.Status],
        "check_fields": ["id", "part", "status", "summary", "measured", "limit", "where", "hint"],
        "views": ["full", "brief"],
    }

    return {
        "schema": "agentcad.api/1",
        "agentcad": __version__,
        "commands": commands,
        "exit_codes": {
            "0": "all pass (WARN allowed)",
            "1": "at least one FAIL",
            "2": "usage or config error",
            "3": "model code raised or produced no valid solid",
        },
        "model": model_info,
        "context": context_methods,
        "config": config,
        "gates": gates,
        "printers": printers_list,
        "templates": list(TEMPLATES),
        "frames": frames,
        "report": report_info,
    }


def cheat_sheet(doc: dict) -> str:
    """Build a compact Markdown cheat sheet from the describe() dict (under 10 KB)."""
    lines = ["# agentcad API cheat sheet", f"*agentcad {doc['agentcad']}, schema {doc['schema']}*", ""]

    # Exit codes
    lines.append("## Exit codes")
    for code, desc in doc["exit_codes"].items():
        lines.append(f"- `{code}`: {desc}")
    lines.append("")

    # Commands: one line each. --json is on almost every command, so it is said once instead of per command.
    lines.append("## Commands (all but `mcp` take `--json`; positional args in CAPS)")
    for cmd in doc["commands"]:
        parts = []
        for arg in cmd["args"]:
            if arg["flags"] == ["--json"]:
                continue
            positional = not arg["flags"][0].startswith("-")
            text = arg["flags"][0].upper() if positional else "/".join(arg["flags"])
            if "choices" in arg:
                text += " {" + "|".join(str(c) for c in arg["choices"]) + "}"
            elif "default" in arg and not isinstance(arg["default"], bool) and arg["default"] not in ([], ""):
                text += f"={arg['default']}"
            parts.append(text if positional and arg.get("required") else f"[{text}]")
        usage = " ".join([f"agentcad {cmd['name']}", *parts])
        lines.append(f"- `{usage}`: {cmd['help']}")
    lines.append("")

    # Context methods
    lines.append("## CheckContext methods")
    for m in doc["context"]:
        # String annotations and self only cost tokens here: "(a: PartRef, b: PartRef) -> float" reads the same.
        signature = re.sub(r"(: |-> )'([^']*)'", r"\1\2", m["signature"]).replace("(self, ", "(").replace("(self)", "()")
        lines.append(f"- `{signature}`: {m['doc']}")
    lines.append("")

    # Config
    lines.append("## Config keys (agentcad.toml)")
    for section, keys in doc["config"].items():
        lines.append(f"### `[{section}]`")
        for key, default in keys.items():
            def_str = f" (default: {default})" if default is not None else ""
            lines.append(f"- `{key}`{def_str}")
        lines.append("")

    # Gates
    lines.append("## Gates (check ids)")
    for gate in doc["gates"]:
        what = f": {gate['what']}" if gate["what"] else ""
        lines.append(f"- `{gate['id']}`{what}")
    lines.append("")

    # Printers
    lines.append("## Printer profiles")
    for p in doc["printers"]:
        bed = "x".join(str(v) for v in p["bed_mm"])
        lines.append(f"- `{p['name']}`: {bed} mm, nozzle {p['nozzle_mm']} mm")
    lines.append("")

    # Templates
    lines.append("## Templates")
    lines.append(", ".join(f"`{t}`" for t in doc["templates"]))
    lines.append("")

    # Frames
    lines.append("## Frames")
    for name, desc in doc["frames"].items():
        lines.append(f"- **{name}**: {desc}")
    lines.append("")

    # Report
    lines.append("## Report schema")
    lines.append(f"- Schema: `{doc['report']['schema']}`")
    lines.append(f"- Statuses: {', '.join(doc['report']['statuses'])}")
    lines.append(f"- Check fields: {', '.join(doc['report']['check_fields'])}")
    lines.append(f"- Views: {', '.join(doc['report']['views'])}")

    result = "\n".join(lines)
    # Ensure under 10 KB
    assert len(result.encode("utf-8")) < 10240, f"cheat sheet is {len(result)} bytes, must be under 10 KB"
    return result


def cli(args: argparse.Namespace) -> int:
    """Handler for ``agentcad api``."""
    doc = describe()
    if getattr(args, "json", False):
        print(json.dumps(doc, separators=(",", ":")))
    else:
        print(cheat_sheet(doc))
    return 0