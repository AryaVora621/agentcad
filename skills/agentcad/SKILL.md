---
name: agentcad
description: Design, check and export 3D parts and flat cut parts in code with CadQuery and agentcad, measuring the built geometry instead of trusting parameters. Use on any task that designs, modifies, checks or exports a 3D part, assembly, print, laser-cut, CNC-routed or waterjet part in code (CadQuery, STEP, STL, 3MF, DXF, SVG), makes a dimensioned technical drawing, including "make a bracket/enclosure/mount/case", "laser cut a panel", fitting parts together, checking printability, or reviewing CAD another agent produced.
---

# agentcad

## 1. The core rule

Never report a dimension, clearance or PASS that you did not measure from the built geometry with agentcad.
Parameters are intent, not evidence. A value you typed into `model.py` tells you what you asked for, not what exists.

## 2. The loop

```bash
agentcad api                               # exact names, signatures, config keys, gates: read it, do not guess
agentcad new my-part --template blank      # or bracket, box-with-lid
# edit my-part/model.py and my-part/agentcad.toml
agentcad check my-part --json --brief      # builds, then measures; only what needs attention
agentcad render my-part                    # build/renders/: one PNG per view plus sheet.png
agentcad view my-part                      # live viewer, for the human
```

If agentcad MCP tools are available (`api`, `check`, `render`, `drawing`, ...), use them instead of shelling out.

A complete `model.py` (the imports and the two functions agentcad looks for; `checks` is optional but never skip it):

```python
import cadquery as cq
from agentcad import Part, Pose

def parts():
    body = cq.Workplane("XY").box(40, 30, 10, centered=(True, True, False))
    body = body.cut(cq.Workplane("XY").center(10, 0).circle(2.2).extrude(10))     # 4.4 mm hole
    return [Part("body", body, material="PETG", print=Pose(rx=0))]               # Part.use / Part.print are Poses

def checks(ctx):
    screw = cq.Workplane("XY").center(10, 0).circle(2.0).extrude(14).translate((0, 0, -2))   # brief: 4.0 mm shank
    ctx.expect("screw_free", ctx.interference("body", screw), max=0.01, unit="mm3", part="body")
```

1. Write the checks for the brief first or while modelling (section 3).
2. Run `agentcad check --json --brief`. It lists only the checks that are not PASS, groups the passing ones by id and
   adds a `hint` to each FAIL or WARN that says what to change. Read `summary`, `measured`, `limit`, `where`, `hint`.
3. Fix the geometry at the location `where` names. Re-run. Do not edit a limit to make a FAIL go away unless the
   requirement itself changed.
4. Run `agentcad render` and open `sheet.png` with an image-capable tool. Renders find gross errors (missing
   feature, wrong side); they never prove thickness, clearance or overlap.
5. Repeat until all gates pass. Run `agentcad check` again after the last edit.

Use the full report (`--json` without `--brief`) for the final check, and whenever you need the numbers of a PASS to
quote them. `--only ID,ID` reruns selected gates; `--no-build` only when `build/` is fresh. The `overhang` gate exempts
downward faces centred below `first_layer_mm` (default 0.3), reported as `first_layer_area_mm2`.

## 3. Turn requirements into checks

Every functional requirement in the brief becomes a check in `checks(ctx)`; probe solids (section 4) stand in for the
mating thing. All names below are the real API.

A fit becomes a play measurement:

```python
def checks(ctx):
    play = ctx.play("lid", "box", direction=(1, 0, 0), max_mm=2.0)
    ctx.expect("lid_side_play", play if play is not None else 2.0, min=0.15, max=0.4, part="lid")
```

"The lid comes off" becomes a slide test:

```python
    sweep = ctx.slide("lid", "box", direction=(0, 0, 1), distance=15.0, steps=40)
    ctx.expect("lid_lifts_free", max(s["overlap_mm3"] for s in sweep), max=0.01, unit="mm3", part="lid")
```

"An M4 screw goes through" becomes a probe cylinder that must not collide and must be close to the wall:

```python
    screw = cq.Workplane("XY").circle(2.0).extrude(30).translate((10, 0, -5))  # 4.0 mm shank
    ctx.expect("screw_interference", ctx.interference("bracket", screw), max=0.01, unit="mm3", part="bracket")
    ctx.expect("screw_wall_gap", ctx.distance("bracket", screw), min=0.0, max=0.5, part="bracket")
```

The distance band proves the screw is actually near the hole, not 14 mm away with zero overlap.

"Fits on the printer" is the built-in `bed_fit` gate: set `[printer]` in `agentcad.toml` to the real machine
(`profile` or `bed = [X, Y, Z]`). "Walls strong enough" is `min_wall_mm` under `[checks]`.

## 4. Probe solids

Build the stand-in for the mating thing from the requirement, never from the model's own parameters. An M4 screw is
a 4.0 mm cylinder. A 54 mm ball is a 54 mm sphere. A cable is a cylinder along its route. A hand is a box at the
size of the hand. If the probe reads the same variable the hole was built from, the check can never fail.

Probes are `cq.Workplane` or `cq.Shape` objects already in the use frame. Place them with `.translate` and pass them
anywhere a part name is accepted (`ctx.interference`, `ctx.distance`, `ctx.slide`, `ctx.play`).

Four patterns that bit real runs (rectangular openings, lift-out with the probe moving, retention scans, probes
read from model constants) are in `references/verification-playbook.md` under "Probe patterns". Read it before
writing checks for a window, a lid, a cradle or a snap.

## 5. Prove a check can fail

A check you have never seen fail is a guess. After writing it: break the geometry (or the probe) so it must trip
(shift the hole 3 mm, clearance to 0, probe 1 mm larger); run `agentcad check` and confirm this check FAILs, on its own
assertion, with the number you expected; restore and confirm PASS.

If it passes while broken, the check measures the wrong thing. Fix the check before trusting any other result.

## 6. Frames

| Frame | Meaning | Used by |
|---|---|---|
| model | the part in its own coordinates | `build/step/<part>.step` |
| use | the assembled pose, `Part.use` | `interference`, probes, `ctx.slide`, `ctx.play`, `build/assembly/` |
| print | on the bed, z = 0 at the bottom, XY centred, `Part.print` rotation | `bed_fit`, `bed_contact`, `overhang`, `min_wall`, `watertight`, `build/stl/` |

Set `Part.print = Pose(rx=180)` (or whatever rotation) so each part prints support-free with layers along the load;
choose the bed face first, then check `overhang`. Bought parts get `printed=False`. Place parts with `Part.use`.

## 7. Reports, exit codes, diff and loose files

Exit codes: 0 all pass (WARN allowed), 1 at least one FAIL, 2 usage or config error (including unknown keys in
`agentcad.toml`), 3 the model code raised or produced no valid solid. Treat 2 and 3 as "no result", not as a pass,
and so is a run with no JSON on stdout (the 1800 s stuck-boolean backstop prints a stack to stderr instead). A project
check function that passes its 300 s budget becomes ERROR; a play probe that starts in contact reports 0 mm and
`starts_in_contact`.

`agentcad check --json` prints one document with `"schema": "agentcad.report/1"` and a `checks` list. Each check has
`id`, `part`, `status` (PASS, WARN, FAIL, SKIP, ERROR), `summary`, `measured`, `limit`, `where`, and on a FAIL or WARN
a `hint`. A project check that raises becomes ERROR and the run continues. Give your own failures a fix with
`ctx.expect(..., hint="...")`; a custom `summary=` keeps the measured value in the text.

- `--brief` keeps the project checks that passed as `passed_checks` (`{"id", "part", "summary"}`, numbers included);
  built-in passes stay folded in `passed`. Every `where` entry has `"frame": "print"` or `"use"`; read coordinates
  in that frame. `ctx.section` returns `loop_list` (per loop `area_mm2`, `bbox_mm`, `perimeter_mm`, `hole`), so a
  window or hole can be measured from its own outline.
- `check` always rebuilds (meta `build_s` shows it, next to `elapsed_s`) and takes seconds to minutes. Batch your
  mutation tests into few runs; run a long one in the background.

Compare builds with `agentcad diff OLD NEW` (two `manifest.json` files or build folders: volume, bounding box, part
set); copy `build/manifest.json` aside before a risky change.

To review CAD you did not write, measure the files directly:

```bash
agentcad measure part.step --json
agentcad interfere a.stl b.stl --json
agentcad overhang part.stl --max-deg 45 --json
agentcad wall part.stl --thin 1.2 --json
agentcad fit part.stl --printer generic-220 --json
```

## 8. Cut parts (laser, CNC, waterjet)

A sheet cut straight through is a cut part. Model it as a 3D plate and mark it:

```python
Part("panel", panel, cut="laser", material="3 mm birch plywood")   # "cnc" or "waterjet" too
```

- The print frame is the cutting frame: sheet on `z = 0`, thickness along `+z`. A plate modelled standing up needs
  `print=Pose(rx=90)` (or whichever rotation lays it flat).
- `[flat]` in `agentcad.toml`: `sheet` (usable `[w, h]` mm), `kerf_mm`, `min_feature_mm`, `tool_diameter_mm`,
  `min_hole_ratio`, `compensate_kerf`. Put the real values in; the defaults are generic.
- Cut parts skip the 3D printing gates and run five: `flat_profile` (a true through-cut), `sheet_fit`,
  `min_feature` (webs and slots), `inner_corners` (CNC only, endmill reach), `hole_size`. A FAIL lists `where` in sheet
  coordinates.
- `agentcad build` writes `build/dxf/<part>.dxf` and `build/svg/<part>.svg` in mm. With `compensate_kerf = true` the
  outline grows and holes shrink by half the kerf in the files; the model and checks stay nominal.
- Fits (tab and slot, press fit) are measured with `ctx.play` and `ctx.interference` on the built solids.
- Read `references/cut-parts.md` before designing one: kerf and press fits, minimum web and slot, hole size against
  thickness, CNC corners and dogbones, tab-and-slot joints, sheet sizes, and how to check each with agentcad.

## 9. Drawings

Make a drawing when a person or a shop needs a dimensioned sheet, or to see that the overall sizes and hole sizes are
what the brief says:

```bash
agentcad drawing my-part --json            # build/drawings/<part>.svg, .pdf, .png
```

Every dimension is measured from the built solid (exact bounding box, circular edges), never from a parameter.
Open the PNG. For numbers, read the JSON: `sheets[].dimensions` entries have `role`, `value_mm` and `count`. Options: `--parts`, `--projection first|third`,
`--sheet A4|A3|letter|tabloid`. No tolerances, section views, slots or angles yet: do not call it a complete
manufacturing drawing, and take clearances from `ctx.play`, not from the sheet.

## 10. When to stop

Stop only when all of these hold:
- all gates pass in the latest `agentcad check` run, run after your last edit;
- every requirement in the brief has a check, and you have seen each one fail (section 5);
- you looked at the renders (and the drawing PNG, if any);
- your summary quotes the measured numbers with units (clearance, wall p1, overhang area, bed fit);
- you state the part count and anything left as a bench check (a snap force, a tolerance on the real printer,
  the real kerf on the real sheet, material behaviour).

## 11. Anti-patterns and the failures they caused

- Reading parameters instead of the built shape: PASS on a dovetail clearance while the part sat 14 mm outside its groove.
- Skipping the print frame: a plate with about 6100 mm2 of unsupported ceiling, because overhang was never checked
  in the print orientation.
- Skipping pairwise interference: two parts overlapped by 50 cm3 in the assembly.
- Coarse export tolerance: a coarse STL of a revolved part sliced a 2.0 mm wall at 1.5 mm. Keep `[export]` at
  0.02 mm and 0.05 rad (the defaults).
- Unmeasured prose: a design doc said outer diameter 29 to 33.5 mm while the model measured 19 mm.
- Gating only on "it builds": every internal channel was sealed and a spout was solid. Sweep a probe along the route.
- Checking only the whole: an assembly was stable while one loose stage alone tipped at 6 degrees.
- Trusting a stale report or a clean render. Re-run after every edit; renders hide overlaps, thin walls and sealed channels.
- Treating a cut part like a print: pockets cannot be cut straight through, and a slot sized without the kerf
  closes up or comes out loose.
- A weak probe: one distance band on a window probe, or a probe read from `USB_W`, passes while the geometry is wrong.

## 12. References

- [design-for-printing.md](references/design-for-printing.md): orientation, walls, fits, screws, snap fits, lids.
- [cut-parts.md](references/cut-parts.md): laser, CNC, waterjet: kerf, minimum features, corners, tab-and-slot, sheets.
- [cadquery-patterns.md](references/cadquery-patterns.md): a failing operation, selectors, shells, fillets, poles and seams.
- [verification-playbook.md](references/verification-playbook.md): planning checks, probe patterns, motion tests,
  reviewing another agent's CAD.
