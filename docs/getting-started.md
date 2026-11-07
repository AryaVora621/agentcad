# Getting started

This tutorial takes you from install to a checked, rendered box with a lid. It takes about ten minutes.

## 1. Install

agentcad is not on PyPI yet. It needs Python 3.10 or newer and installs CadQuery 2.8.

```bash
uv tool install git+https://github.com/AryaVora621/agentcad
# or
pip install git+https://github.com/AryaVora621/agentcad
```

Confirm the install and the render stack:

```bash
agentcad doctor
```

`agentcad doctor` prints the agentcad and CadQuery versions, whether offscreen rendering works, and whether it can
write to the current folder. Fix anything it flags before continuing.

## 2. Create a project

```bash
agentcad new my-box --template box-with-lid
cd my-box
```

The template creates `agentcad.toml`, `model.py` and a short `README.md`.

## 3. Tour of the project

`agentcad.toml` holds the project configuration:

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
```

- `project.module` is the only required key: the Python file that defines `parts()`.
- `printer.profile` names a built-in profile (`agentcad printers` lists them). Use `bed = [X, Y, Z]` for your own
  machine.
- `checks` sets the thresholds for the built-in gates. The overhang gate also ignores downward faces centred below
  `first_layer_mm` (default 0.3), such as a chamfer on the bottom edge, and reports that area. Unknown keys are an error (exit code 2), so a typo cannot
  silently turn a gate off.

`model.py` defines the parts. The box is 60 x 40 x 30 mm, open at the top, with 2 mm walls and floor. The lid is a
plate with a hollow 6 mm lip that drops into the box, with 0.25 mm of clearance per side. The lid prints upside down
(`Part.print = Pose(rx=180)`) so its plate lies flat on the bed and the lip points up.

```python
from agentcad import Part, Pose

def parts():
    return [
        Part("box", box_shape),
        Part("lid", lid_shape, use=Pose(z=30), print=Pose(rx=180)),
    ]
```

Here `box_shape` and `lid_shape` stand for the CadQuery workplanes built earlier in the file. `use` is the lid's
pose in the assembled product (resting on the rim at z = 30 mm); `print` is the rotation used for printing.

The file also defines `checks(ctx)`, which holds the project's own requirements. For the lid:

```python
def checks(ctx):
    lift = ctx.slide("lid", "box", (0, 0, 1), 20, steps=20)
    ctx.expect("lid_lifts_free", max(step["overlap_mm3"] for step in lift), max=0.001, part="lid", unit="mm3")
    for axis, direction in (("x", (1, 0, 0)), ("y", (0, 1, 0))):
        play = ctx.play("lid", "box", direction, max_mm=2.0)
        ctx.expect(f"lid_play_{axis}", 2.0 if play is None else play, min=0.15, max=0.40, part="lid")
```

`ctx.slide` lifts the built lid straight up and measures the overlap at each step. `ctx.play` slides the built lid
sideways until it touches the box and returns the free travel in millimetres. That is the clearance of the fit,
measured from the solids. A lid that never touches within 2 mm is far too loose, so the check reports 2.0 and fails
the limit.

## 4. Run the checks

```bash
agentcad check
```

You get one line per check with its status, the part, and a one-line summary, followed by a total of passes, warnings
and failures. The exit code is 0 when everything passes. For agents, add `--json` to get the same data as one
JSON document.

## 5. Read a failure

Introduce a deliberate mistake. In `model.py`, set `CLEARANCE` to 0 so the lip is as wide as the cavity, then
run the check again:

```bash
agentcad check
```

The two play checks now fail:

```text
FAIL   lid_play_x      lid      0.00125 mm (outside min 0.15 and max 0.4)
FAIL   lid_play_y      lid      0.00125 mm (outside min 0.15 and max 0.4)
```

The lid cannot move sideways at all, so the measured play is about 0 mm and the limit requires at least 0.15 mm. The
summary names the check, the part (`lid`), the measured value and the limit. The interference gate still passes,
because the lip only touches the walls. The exit code is 1.

Read the failing lines, set `CLEARANCE` back to 0.25, and run `agentcad check` again. The play checks pass. This is
the loop: a requirement written as a check, measured from the geometry, fails when the geometry is wrong.

## 6. Render

```bash
agentcad render
```

This writes one PNG per view and a combined `sheet.png` into `build/renders/`, each labelled with the view name, overall size and a scale
bar. Open `sheet.png` and look at the parts. Renders show gross errors, such as a missing feature or a lid the wrong
way up. They do not prove thickness, clearance or overlap; the checks do.

## 7. Open the viewer

```bash
agentcad view
```

This starts a local viewer (loopback only) and opens it in your browser. It reloads when the build files change, so
you can keep it open while you edit: run `agentcad check` or `agentcad build` after an edit and the model updates.

## 8. Files for the slicer

`agentcad check` builds the project into `build/`:

| Path | Contents | Use |
|---|---|---|
| `build/stl/<part>.stl` | each part in its print orientation, on the bed | open in the slicer |
| `build/step/<part>.step` | each part in its own frame | CAD exchange, machining |
| `build/assembly/<part>.stl` | each part in the assembled pose | visual review, interference |
| `build/assembly.step` | the whole assembly, one named solid per part | CAD exchange |
| `build/manifest.json` | volumes, bounding boxes, hashes | `agentcad diff OLD NEW` (manifests or build folders), audit |
| `build/report.json` | the last `agentcad check` report | agents, CI |
| `build/renders/` | PNG views from `agentcad render` | review |

Load the STL from `build/stl/` into your slicer. Parts are already oriented and sitting on z = 0. Check the part
count against the `qty` field in `Part`.

## Next steps

- Write your own checks. Start from a requirement ("the lid lifts off", "an M4 screw passes through"), build a probe solid
  for the mating thing, and assert on the measurement. Prove each check can fail.
- Use it from an agent. Copy or symlink `skills/agentcad` into `~/.claude/skills/` (Claude Code), or point your agent's
  instructions file at `skills/agentcad/SKILL.md`. The skill teaches the loop and the traps.
- Read the [CLI reference](cli.md) and the [report schema](report-schema.md).
