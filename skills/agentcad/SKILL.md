---
name: agentcad
description: Design, check and export 3D parts in code with CadQuery and agentcad, measuring the built geometry instead of trusting parameters. Use on any task that designs, modifies, checks or exports a 3D part, assembly or print in code (CadQuery, STEP, STL, 3MF), including "make a bracket/enclosure/mount/case", fitting parts together, checking printability, or reviewing CAD another agent produced.
---

# agentcad

## 1. The core rule

Never report a dimension, clearance or PASS that you did not measure from the built geometry with agentcad.
Parameters are intent, not evidence. A value you typed into `model.py` tells you what you asked for, not what exists.

## 2. The loop

```bash
agentcad new my-part --template blank      # or bracket, box-with-lid
# edit my-part/model.py and my-part/agentcad.toml
agentcad check my-part --json              # builds, then measures
agentcad render my-part                    # build/renders/: one PNG per view plus sheet.png
agentcad view my-part                      # live viewer, for the human
```

1. Write the checks for the brief first or while modelling (section 3).
2. Run `agentcad check --json`. Read every FAIL: `summary`, `measured`, `limit`, `where`.
3. Fix the geometry at the location `where` names. Re-run. Do not edit a limit to make a FAIL go away unless the
   requirement itself changed.
4. Run `agentcad render` and open `sheet.png` with an image-capable tool. Look at it before saying it looks right.
   Renders find gross errors (missing feature, wrong side); they never prove thickness, clearance or overlap.
5. Repeat until all gates pass. Run `agentcad check` again after the last edit.

The `overhang` gate ignores faces on the bed and downward faces centred below `first_layer_mm` (default 0.3), so a
bottom-edge chamfer does not count; the exempt area is reported as `first_layer_area_mm2`.

Use `--only ID,ID` to rerun selected gates and `--no-build` only when `build/` is fresh.

## 3. Turn requirements into checks

Every functional requirement in the brief becomes a check in `checks(ctx)`. Probe solids (section 4) stand in for the
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
    import cadquery as cq
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

## 5. Prove a check can fail

A check you have never seen fail is a guess. After writing it:

1. Break the geometry (or the probe) in a way that must trip it: shift the hole 3 mm, shrink the clearance to 0,
   make the probe 1 mm larger.
2. Run `agentcad check`. Confirm that this check FAILs, on its own assertion, with the number you expected.
3. Restore the geometry and confirm PASS.

If it passes while broken, the check measures the wrong thing. Fix the check before trusting any other result.

## 6. Frames

| Frame | Meaning | Used by |
|---|---|---|
| model | the part in its own coordinates | `build/step/<part>.step` |
| use | the assembled pose, `Part.use` | `interference`, probes, `ctx.slide`, `ctx.play`, `build/assembly/` |
| print | on the bed, z = 0 at the bottom, XY centred, `Part.print` rotation | `bed_fit`, `bed_contact`, `overhang`, `min_wall`, `watertight`, `build/stl/` |

Set `Part.print = Pose(rx=180)` (or whatever rotation) so each part prints support-free and with layers along the
load. Choose the face on the bed first, then check `overhang` in that frame. Bought parts get `printed=False`.
Model parts in a convenient local frame and place them with `Part.use`.

## 7. Reports, exit codes, diff and loose files

Exit codes: 0 all pass (WARN allowed), 1 at least one FAIL, 2 usage or config error (including unknown keys in
`agentcad.toml`), 3 the model code raised or produced no valid solid. Treat 2 and 3 as "no result", not as a pass.

`agentcad check --json` prints one document with `"schema": "agentcad.report/1"` and a `checks` list. Each check has
`id`, `part`, `status` (PASS, WARN, FAIL, SKIP, ERROR), `summary`, `measured`, `limit`, `where`. A project check that
raises becomes ERROR and the run continues.

Compare builds with `agentcad diff OLD NEW` (two `manifest.json` files or two build folders; volume, bounding box,
part set) to catch
unintended changes after an edit. Copy `build/manifest.json` aside before a risky change.

To review CAD you did not write, measure the files directly:

```bash
agentcad measure part.step --json
agentcad interfere a.stl b.stl --json
agentcad overhang part.stl --max-deg 45 --json
agentcad wall part.stl --thin 1.2 --json
agentcad fit part.stl --printer generic-220 --json
```

## 8. When to stop

Stop only when all of these hold:

- all gates pass in the latest `agentcad check` run, run after your last edit;
- every requirement in the brief has a check, and you have seen each one fail (section 5);
- you looked at the renders;
- your summary quotes the measured numbers with units (clearance, wall p1, overhang area, bed fit);
- you state the part count and anything left as a bench check (a snap force, a tolerance on the real printer,
  material behaviour).

## 9. Anti-patterns and the failures they caused

- Reading parameters instead of the built shape. A worker reported PASS on a sliding dovetail clearance while the
  part sat 14 mm outside its groove, because its check read the parameters.
- Skipping the print frame. A plate was modelled to print face down with about 6100 mm2 of unsupported ceiling,
  because overhang was never checked in the print orientation.
- Skipping pairwise interference. Two parts overlapped by 50 cm3 in the assembly, because nobody ran interference in
  the use pose.
- Coarse export tolerance. A coarse STL export of a revolved part produced a facet that sliced a 2.0 mm wall at
  1.5 mm. Fix: export tolerance 0.02 mm, angular tolerance 0.05 rad (the defaults in `[export]`).
- Unmeasured prose. A design doc stated an outer diameter of 29 to 33.5 mm while the model measured 19 mm. Measure
  every number you write into prose.
- Gating only on "it builds". A rebuild passed its only gate while every internal channel was sealed and a spout was
  solid. Trace the functional path: sweep a probe along the water, cable or air route.
- Checking only the whole. An assembly was stable overall while one loose stage alone tipped at 6 degrees. Check each
  loose sub-stack.
- Trusting a stale report. A gate report was stale because the build crashed after the old report was written.
  agentcad builds atomically, but still compare report timestamps with your last edit and re-run after every edit.
- Trusting the picture. A clean render hid all of the above.

## 10. References

- [references/design-for-printing.md](references/design-for-printing.md): read before choosing orientation, wall
  thickness, fits, screw holes or snap features for an FDM part.
- [references/cadquery-patterns.md](references/cadquery-patterns.md): read when a CadQuery operation fails, returns an
  unexpected shape, or you need selectors, shells, fillets or probe solids.
- [references/verification-playbook.md](references/verification-playbook.md): read when planning checks for a brief,
  testing motion or function, or reviewing another agent's CAD.
