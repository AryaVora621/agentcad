# agentcad

CAD for AI agents: model parts in Python, then measure the solids that were actually built.

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
3. Read the JSON. Every FAIL says what was measured, the limit, and where.
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

## Use it with your agent

Every command takes `--json` and uses the exit codes 0 (all checks pass, WARN allowed), 1 (at least one FAIL), 2 (usage
or config error) and 3 (the model code raised or produced no valid solid).

- Claude Code: copy or symlink `skills/agentcad` into `~/.claude/skills/` or into the project's `.claude/skills/`.
- Other agents (Codex, Cursor and others): point the agent's instructions file, such as `AGENTS.md`, at
  `skills/agentcad/SKILL.md`.

The skill teaches the loop, how to turn each requirement into a check, and how to prove a check can fail. Its reference
files cover [design for printing](skills/agentcad/references/design-for-printing.md),
[CadQuery patterns](skills/agentcad/references/cadquery-patterns.md) and the
[verification playbook](skills/agentcad/references/verification-playbook.md).

For a step-by-step introduction see [docs/getting-started.md](docs/getting-started.md).

## Commands

| Command | Purpose |
|---|---|
| `agentcad new DIR` | scaffold a project from a template (`blank`, `bracket`, `box-with-lid`) |
| `agentcad build` | build parts and write STEP, STL, assembly files and `manifest.json` |
| `agentcad check` | build, then run the built-in gates and the project's `checks(ctx)` |
| `agentcad measure FILE...` | bounding box, volume, area, centre of mass, solid count and validity |
| `agentcad interfere FILE...` | pairwise overlap between files as placed |
| `agentcad overhang FILE...` | unsupported overhang area of loose files |
| `agentcad wall FILE...` | ray-cast wall thickness of loose files |
| `agentcad fit FILE...` | does it fit a printer or a bed size |
| `agentcad render` | offscreen multi-view sheet and section views |
| `agentcad view` | local live viewer |
| `agentcad diff OLD NEW` | compare two builds (`manifest.json` files or build folders) |
| `agentcad printers` | list printer profiles |
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

- MCP server (`agentcad mcp`) exposing build, check, measure and render as tools, returning render PNGs inline.
- Sliding explode view in the viewer, with per-part explode vectors from the manifest.
- Slicer gate (OrcaSlicer or PrusaSlicer CLI) for support volume and print time.
- build123d adapter for `Part.shape`.

## Contributing

```bash
git clone https://github.com/AryaVora621/agentcad
cd agentcad
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
```

Every new gate needs a fixture in `tests/` that makes it FAIL, next to one that makes it PASS. If code and
`docs/ARCHITECTURE.md` disagree, fix one of them in the same change.

## License

agentcad is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE). It is free for personal
projects, hobby work, study, research, teaching, and use by charities, schools and public institutions. Commercial
use, including use inside a company, in paid client work or in a commercial product or service, needs a paid license:
see [COMMERCIAL.md](COMMERCIAL.md) or email [arya@arya-vora.org](mailto:arya@arya-vora.org).
