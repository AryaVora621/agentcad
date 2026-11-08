# agentcad

CAD for AI agents: model parts in Python, then measure the solids that were actually built.

An agent that uses agentcad gets:

- **Measured verdicts.** `agentcad check` builds the model and returns PASS or FAIL with the number, the limit and
  the location, so "it fits" is backed by a measurement and not by a parameter.
- **Token-lean feedback.** `--json --brief` returns only what needs attention, each FAIL with a one-line fix hint.
  On the bundled failing example the report drops from 17.9 KB to 3.1 KB.
- **3D prints and cut parts.** FDM gates (bed fit, overhang, wall thickness, interference, motion) and, for laser,
  CNC and waterjet parts, flat-profile gates plus DXF and SVG output.
- **Drawings.** Dimensioned SVG, PDF and PNG sheets whose every number is read from the built solid.
- **A skill and an MCP server.** One command installs the workflow into Claude Code, Codex, Cursor or AGENTS.md;
  the MCP server exposes the same tools without shelling out.
- **No guessing.** `agentcad api` prints every command, helper, config key and gate in one document.

## Agents start here

```bash
# 1. Install (Python 3.11 or newer). Add the mcp extra only if your agent speaks MCP.
uv tool install git+https://github.com/AryaVora621/agentcad
uv tool install 'agentcad[mcp] @ git+https://github.com/AryaVora621/agentcad'   # instead of the line above, with MCP

# 2. Teach the agent the workflow (Claude Code by default; --host codex|cursor|agents-md for others)
agentcad skill install

# 3. Optional: register the MCP server (details in docs/mcp.md)
claude mcp add agentcad -- agentcad mcp

# 4. The loop
agentcad api                                   # names, signatures, config keys, gates: do not guess
agentcad new my-part --template blank          # then edit my-part/model.py and my-part/agentcad.toml
agentcad check my-part --json --brief          # build, measure, read each FAIL and its hint
agentcad render my-part                        # look at build/renders/sheet.png
agentcad check my-part                         # once more after the last edit
```

Exit codes: 0 pass (WARN allowed), 1 a gate failed, 2 usage or config error, 3 the model code failed to build.
Use the full `--json` report when you need every `measured` and `limit` value, and `--brief` for the edit loop.

## Why

Language models can write CAD code, but they cannot see what it builds. Left alone, an agent "verifies" a part by
reading its own parameters back, passes tests that compare a value with itself, and reports success while parts
overlap, print upside down, or have walls too thin to slice. Renders do not help much: thin walls, hidden overlaps and
missing clearances are invisible in a screenshot. agentcad closes the loop. It builds the part, exports STEP and STL,
and measures the built solids. Every check returns numbers, a limit, a status and, where it applies, a location the
agent can act on.

## How it works

```text
  model.py (CadQuery)
        |
        v
  agentcad build ---> build/step, build/stl, build/assembly, manifest.json
        |
        v
  agentcad check ---> report.json (numeric, located PASS / FAIL)
        |
        v
  read FAILs, fix model.py ---> agentcad render (look at sheet.png) ---> repeat
```

1. Model each part in code with CadQuery.
2. `agentcad check` builds the project and measures the built solids.
3. Read the JSON (`--json --brief` to keep it short). Every FAIL says what was measured, the limit, where, and a hint.
4. Fix the model and run the check again.
5. Render the result and look at it. Use `agentcad view` for a live viewer.

## Quickstart

agentcad is not on PyPI yet. Install from GitHub (Python 3.11 or newer, because CadQuery 2.8 requires it):

```bash
uv tool install git+https://github.com/AryaVora621/agentcad
# or
pip install git+https://github.com/AryaVora621/agentcad
```

Create a project, check it, render it, and open the viewer:

```bash
agentcad new my-box --template box-with-lid
agentcad check my-box
agentcad render my-box
agentcad view my-box
```

Sample output of `agentcad check` on the bundled example:

```text
PASS   valid           box      valid solid, 15552.0 mm3
PASS   valid           lid      valid solid, 6888.0 mm3
PASS   single_solid    box      one solid
PASS   single_solid    lid      one solid
PASS   watertight      box      watertight, 28 triangles
PASS   watertight      lid      watertight, 44 triangles
PASS   bed_fit         box      60.0 x 40.0 x 30.0 mm fits generic-220 220x220x250
PASS   bed_fit         lid      60.0 x 40.0 x 8.0 mm fits generic-220 220x220x250
PASS   bed_contact     box      2400.0 mm2 on the bed (100% of footprint)
PASS   bed_contact     lid      2400.0 mm2 on the bed (100% of footprint)
PASS   overhang        box      0.0 mm2 unsupported at 45 deg (allowed 25)
PASS   overhang        lid      0.0 mm2 unsupported at 45 deg (allowed 25)
SKIP   min_wall        -        disabled; set checks.min_wall_mm to enable
PASS   interference    box+lid  overlap 0.000 mm3
PASS   lid_lifts_free  lid      0 mm3 (within max 0.001)
PASS   lid_play_x      lid      0.2506 mm (within min 0.15 and max 0.4)
PASS   lid_play_y      lid      0.2506 mm (within min 0.15 and max 0.4)
16 pass, 0 warn, 0 fail, 1 skip
```

## What gets checked

`agentcad check` builds the project, then runs these gates:

| id | Scope | FAIL when |
|---|---|---|
| `valid` | each part | the shape is not valid, has no solid, or has volume <= 0 (ERROR when `parts()` raises) |
| `single_solid` | each part | WARN when a part is more than one disconnected solid |
| `watertight` | each part | the exported print STL is not watertight or not winding-consistent |
| `bed_fit` | each printed part | the print-frame bounding box does not fit the printer in either XY orientation |
| `bed_contact` | each printed part | WARN only: little area touches the bed (likely to tip or detach) |
| `overhang` | each printed part | unsupported area exceeds `overhang_allow_mm2`; faces centred below `first_layer_mm` (default 0.3), such as a bottom-edge chamfer, are exempt and reported as `first_layer_area_mm2` |
| `min_wall` | each printed part | enabled by `min_wall_mm > 0`; FAIL when the 1st percentile wall thickness is below it |
| `interference` | each part pair | use-pose overlap exceeds `interference_tol_mm3`, unless the pair is in `allow_overlap`; when the geometry kernel disagrees with itself, the larger overlap is reported |

Parts with `cut` set (`laser`, `cnc` or `waterjet`) are sheet parts. They skip the five 3D-printing gates
(`watertight`, `bed_fit`, `bed_contact`, `overhang`, `min_wall`) and run these instead, each on a section of the built
solid at half the thickness (see [docs/flat.md](docs/flat.md)):

| id | FAIL or WARN when |
|---|---|
| `flat_profile` | FAIL: the part is not a straight through-cut (volume differs from profile area x thickness by more than 1 percent, or the sections at 10 and 90 percent of the thickness differ from the middle one) |
| `sheet_fit` | FAIL: the outline fits `[flat].sheet` neither as is nor turned 90 degrees |
| `min_feature` | FAIL: a web or tab is narrower than `min_feature_mm`, or a slot narrower than `kerf_mm` |
| `inner_corners` | WARN, CNC only: inside corners and slots a `tool_diameter_mm` endmill cannot reach |
| `hole_size` | WARN: a circular hole is narrower than `min_hole_ratio` times the thickness |

Project checks go further. They are written in `checks(ctx)` and measure against probe solids (stand-ins for the
mating thing, such as an M4 screw or a 54 mm ball) and motion tests: `ctx.slide` sweeps a part along a direction and
reports overlap at each step, and `ctx.play` returns the free travel before contact, which is the clearance of a fit.

## The project contract

A project is a folder with an `agentcad.toml` and a Python module.

```toml
[project]
name = "box-with-lid"
module = "model.py"

[printer]
profile = "generic-220"

[export]
tolerance = 0.02
angular_tolerance = 0.05

[checks]
max_overhang_deg = 45
overhang_allow_mm2 = 25
first_layer_mm = 0.3
min_wall_mm = 1.2
allow_overlap = []
```

`model.py` defines `parts()` and may define `checks(ctx)`:

```python
import cadquery as cq
from agentcad import Part, Pose

CLEARANCE = 0.25  # mm per side between lid lip and box wall


def parts():
    box = (
        cq.Workplane("XY")
        .box(60, 40, 30, centered=(True, True, False))
        .faces(">Z")
        .shell(-2)
    )
    plate = cq.Workplane("XY").box(60, 40, 2, centered=(True, True, False))
    lip = (
        cq.Workplane("XY")
        .box(56 - 2 * CLEARANCE, 36 - 2 * CLEARANCE, 6, centered=(True, True, False))
        .translate((0, 0, -6))
    )
    lid = plate.union(lip)
    return [
        Part("box", box),
        # Lid sits on the rim in the assembly; it prints upside down so the plate is on the bed.
        Part("lid", lid, use=Pose(z=30), print=Pose(rx=180)),
    ]


def checks(ctx):
    play = ctx.play("lid", "box", direction=(1, 0, 0), max_mm=2.0)
    ctx.expect("lid_side_play", play if play is not None else 2.0, min=0.15, max=0.4, part="lid")
    lift = ctx.slide("lid", "box", direction=(0, 0, 1), distance=10.0, steps=40)
    ctx.expect(
        "lid_lifts_free",
        max(step["overlap_mm3"] for step in lift),
        max=0.01,
        part="lid",
        unit="mm3",
    )
```

## Cut parts and drawings

Mark a part as a sheet part and agentcad treats it as a 2D cut: it checks the profile against what the cutter can do
and writes a DXF and an SVG in millimetres.

```python
Part("panel", panel_shape, cut="laser", material="3 mm birch plywood")   # or "cnc" or "waterjet"
```

```toml
[flat]
sheet = [600, 400]      # usable sheet, mm
kerf_mm = 0.15
min_feature_mm = 1.0
compensate_kerf = true  # export the outline grown by kerf/2 and the holes shrunk by it
```

`agentcad build` writes `build/dxf/<part>.dxf` and `build/svg/<part>.svg`. The sheet lies on `z = 0` in the cutting
frame (`Part.print`), and the thickness runs along `+z`. Try it on `examples/flat_panel`; the gates and limits are in
[docs/flat.md](docs/flat.md).

`agentcad drawing PROJECT` writes one dimensioned sheet per part as SVG, PDF and PNG with front, top, right and
isometric views, hole callouts and a title block. Every dimension is read from the built solid
([docs/drawings.md](docs/drawings.md)).

## Use it with your agent

Every command takes `--json` and uses the exit codes 0 (all checks pass, WARN allowed), 1 (at least one FAIL), 2 (usage
or config error) and 3 (the model code raised or produced no valid solid).

**Install the skill** with `agentcad skill install [--host HOST] [--project DIR] [--link] [--force]`:

| Host | Installs | Where |
|---|---|---|
| `claude` (default) | the skill folder | `~/.claude/skills/agentcad`, or `DIR/.claude/skills/agentcad` with `--project DIR` |
| `codex` | the skill folder | `$CODEX_HOME/skills/agentcad` (default `~/.codex/skills/agentcad`); user-wide only, no `--link` |
| `cursor` | a rule and its references | `.cursor/rules/agentcad.mdc` and `.cursor/rules/agentcad-references/` in `--project` (default: the current folder) |
| `agents-md` | a condensed guide between `<!-- agentcad:begin -->` and `<!-- agentcad:end -->` markers | `AGENTS.md` in `--project` (default: the current folder) |

An install that would change an existing, different copy needs `--force`. `--link` (Claude Code only) symlinks to this
install so the skill stays current. Nothing is ever written through a symlink inside a project.
`agentcad skill show` prints the skill, `agentcad skill show --reference NAME` one reference, `agentcad skill path`
where it lives.

**MCP.** `agentcad mcp [--root DIR]` serves the CLI as MCP tools over stdio; see [docs/mcp.md](docs/mcp.md).
**No guessing.** `agentcad api` (or `agentcad api --json`) lists commands, `ctx` helpers, config keys and gates.

The skill teaches the loop, how to turn each requirement into a check, and how to prove a check can fail. Its
references cover [design for printing](skills/agentcad/references/design-for-printing.md),
[cut parts](skills/agentcad/references/cut-parts.md),
[CadQuery patterns](skills/agentcad/references/cadquery-patterns.md) and the
[verification playbook](skills/agentcad/references/verification-playbook.md).

For a step-by-step introduction see [docs/getting-started.md](docs/getting-started.md).

## Commands

| Command | Purpose |
|---|---|
| `agentcad new DIR` | scaffold a project from a template (`blank`, `bracket`, `box-with-lid`) |
| `agentcad build` | build parts and write STEP, STL, assembly files and `manifest.json` (plus DXF and SVG for cut parts) |
| `agentcad check` | build, then run the built-in gates and the project's `checks(ctx)`; `--brief` for a short report with fix hints |
| `agentcad measure FILE...` | bounding box, volume, area, centre of mass, solid count and validity |
| `agentcad interfere FILE...` | pairwise overlap between files as placed |
| `agentcad overhang FILE...` | unsupported overhang area of loose files |
| `agentcad wall FILE...` | ray-cast wall thickness of loose files |
| `agentcad fit FILE...` | does it fit a printer or a bed size |
| `agentcad render` | offscreen multi-view sheet and section views |
| `agentcad view` | local live viewer |
| `agentcad diff OLD NEW` | compare two builds (`manifest.json` files or build folders) |
| `agentcad printers` | list printer profiles |
| `agentcad drawing` | dimensioned SVG, PDF and PNG sheets from the built solids |
| `agentcad api` | every command, ctx helper, config key and gate in one document |
| `agentcad skill` | `install`, `show` or `path` for the agent skill (Claude Code, Codex, Cursor, AGENTS.md) |
| `agentcad mcp` | MCP server on stdio (needs the `mcp` extra) |
| `agentcad doctor` | versions, offscreen render check, write access |

Full option lists are in [docs/cli.md](docs/cli.md).

## Reports

`agentcad check --json` prints one JSON document with the schema tag `agentcad.report/1` and writes it to
`build/report.json`. Each entry in `checks` looks like this (one check from a real run on `examples/box_with_lid`):

```json
{
  "id": "lid_play_x",
  "part": "lid",
  "status": "PASS",
  "summary": "0.2506 mm (within min 0.15 and max 0.4)",
  "measured": {
    "value_mm": 0.2506
  },
  "limit": {
    "min_mm": 0.15,
    "max_mm": 0.4
  },
  "where": []
}
```

With `--brief` the report keeps only the checks that are not PASS, adds a `hint` to each FAIL or WARN, and lists the
passing checks grouped by id. From `agentcad check examples/broken --brief` (text form, one check):

```text
FAIL   min_wall        thin_cup    p1 0.59 mm < 1.2 mm; 95.4% of surface thin, z 0.0..20.0
  hint: thicken the thin walls to at least 1.2 mm (thinnest 0.59 mm, near z 0.0..20.0); lower checks.min_wall_mm only if the nozzle can print the thinner wall
```

The full schema is in [docs/report-schema.md](docs/report-schema.md).

## Design principles

1. Measure, never echo. A check reads the built solid or the exported mesh, never the parameters that produced it.
2. Machine-readable first. Every command takes `--json` and emits one JSON document on stdout with a versioned schema.
   Human output is a compact table. Logs go to stderr.
3. Actionable failures. A FAIL says what was measured, what the limit was, and where.
4. Deterministic and headless. No GUI is needed except for `agentcad view`. Random sampling is seeded.
5. Exit codes are the API: 0 pass, 1 FAIL, 2 usage or config error, 3 model code error.
6. Every gate can fail. Each built-in check ships with a test fixture that must FAIL it.

## Roadmap

- Sliding explode view in the viewer, with per-part explode vectors from the manifest.
- Slicer gate (OrcaSlicer or PrusaSlicer CLI) for support volume and print time.
- build123d adapter for `Part.shape`.
- Nesting several cut parts on one sheet.
- Section views and tolerances on drawings.

## Contributing

```bash
git clone https://github.com/AryaVora621/agentcad
cd agentcad
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,mcp]"
.venv/bin/python -m pytest -q
```

Every new gate needs a fixture in `tests/` that makes it FAIL, next to one that makes it PASS. If code and
`docs/ARCHITECTURE.md` disagree, fix one of them in the same change.

## License

agentcad is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). It is free for personal
projects, hobby work, study, research, teaching, and use by charities, schools and public institutions. Commercial
use, including use inside a company, in paid client work or in a commercial product or service, needs a paid license:
see [COMMERCIAL.md](COMMERCIAL.md) or email [arya@arya-vora.org](mailto:arya@arya-vora.org).
