# Verification playbook

How to verify generated CAD the way an engineer would: state the requirement, measure the built solid against it, and
confirm the check can fail.

## Requirement to check

Write the check from the requirement in the brief, not from the code that satisfies it.

| Requirement | Check |
|---|---|
| The lid fits with 0.2 to 0.4 mm side play | `ctx.play("lid", "box", direction=(1, 0, 0))` then `ctx.expect(..., min=0.2, max=0.4)` |
| The lid lifts off | `ctx.slide("lid", "box", (0, 0, 1), distance=15, steps=40)`, expect max overlap about 0 |
| An M4 screw passes through | probe cylinder 4.0 mm: `ctx.interference` max 0.01 mm3 and `ctx.distance` in a band |
| A 54 mm ball sits in the cradle | probe sphere: interference 0, distance to the cradle in a small band |
| The part fits the printer | built-in `bed_fit` with the real `[printer]` |
| The wall holds a load | `min_wall_mm` in `[checks]`, plus `agentcad wall --thin` for where |
| No collisions in the assembly | built-in `interference` (it errs toward FAIL when the geometry kernel disagrees with itself), listing allowed pairs in `allow_overlap` |
| Prints without support | built-in `overhang` in the print frame |

If a requirement has no row here, invent the nearest measurable proxy and say what it proves and what it does not.

## Probe solids

Build the probe from the requirement, never from the model's own parameters. An M4 screw is a 4.0 mm cylinder; a
54 mm ball is a 54 mm sphere. If the probe and the hole share one variable, the check cannot fail.

Use two measurements for a fit: `interference == 0` proves nothing collides, and a `distance` band proves the probe is
actually near the feature rather than 14 mm away. Both are needed.

## Probe patterns

Four traps from real runs. Each pattern includes the break that proves the check.

**Rectangular opening: width and height probes.** One distance band on a full-size probe still passes when the
opening grows, because the probe keeps touching the edges that did not move (a 58 x 44 window grown to 60 wide still
read 0 mm). Use two probes that each touch one pair of edges, plus a no-collision probe at full size:

```python
fit = cq.Workplane("XY").box(58, 44, 20).translate((-20, 0, 0))                 # the display, from the brief
wide = cq.Workplane("XY").box(58, 30, 20).translate((-20, 0, 0))                # touches the left and right edges
tall = cq.Workplane("XY").box(40, 44, 20).translate((-20, 0, 0))                # touches the top and bottom edges
ctx.expect("window_fits", ctx.interference("front", fit), max=0.01, unit="mm3", part="front")
ctx.expect("window_width_tight", ctx.distance("front", wide), max=0.3, part="front")
ctx.expect("window_height_tight", ctx.distance("front", tall), max=0.3, part="front")
```

Grow the width to 60 and `window_width_tight` reads 1.0 mm and FAILs. A hole that is a loose fit works the same way
(a band per axis). `ctx.section(...)["loop_list"]` gives each loop's `bbox_mm` and `hole`, which measures the
opening from its own outline.

**Lift-out and drop-in: the probe moves, the part stays.** `ctx.slide(moving, against, ...)` moves the first
argument. For a ball that lifts out of a cradle, pass the ball first: `ctx.slide(ball, "mount", (0, 0, 1), distance=60)`.
Passing the mount moves the part away from its own probe and reports a huge overlap that means nothing. The same goes
for a cable, a card or a plug entering a slot.

**Retention ("cannot roll off", "lid stays shut"): scan until free.** A single check in a raised pose is not
retention: with the rim lowered by 2 mm it still passed, because the probe at the chosen height did not touch the
lowered rim. Lift the probe in steps and report the first height with zero overlap:

```python
sweep = ctx.slide(ball, "mount", (0, 0, 1), distance=60, steps=120)
free_at = next((s["offset_mm"] for s in sweep if s["overlap_mm3"] <= 0.01), 60.0)
ctx.expect("ball_escape_lift", free_at, min=30.0, unit="mm", part="mount")      # must rise 30 mm before it is free
```

Each sweep entry is `{"offset_mm", "overlap_mm3"}`. Lower the rim and confirm the
escape height drops below the limit. For a latch do the same along the opening direction.

**Probe built from a model constant.** An 8 mm USB opening passed the "9 x 4 plug fits" check because the probe read
the model's own `USB_W`, so probe and hole changed together. Type the brief's numbers into the probe (`9.0`, `4.0`),
never `USB_W`. Add an oversize probe (`9.2 x 4.2` must collide) so an opening that grew also FAILs.

## Motion tests

- Lid, drawer, sliding dovetail: `ctx.slide(moving, against, direction, distance, steps)` returns the overlap at each
  offset. For a part that must come free, the overlap must be about 0 at every step.
- Latch or snap barb: the overlap should be non-zero only at the barb along its travel. Assert both: zero overlap before
  and after the barb, and non-zero at it. A latch that overlaps everywhere is a press fit, not a latch.
- Pin in a hole, lid side play: `ctx.play(moving, against, direction, max_mm)` returns the free travel before contact,
  which is the clearance in that direction. `None` means it never touches within `max_mm`; handle that case explicitly
  (a missing contact is not a pass for a snug fit).
- Test the directions the user will use, and the opposite directions. A part that slides out `+X` but binds in `-X`
  is a finding.

## Clearance by section

When a direction test is not enough (a part with several mating surfaces, a gap you want to see around the whole
perimeter), cut the assembly: `ctx.section(names, axis, at)` returns the area, bounding box and loops of a planar cut,
and `agentcad render --section AXIS=MM` shows it. Use sections to find where the gap is, then use `ctx.distance` to put a
number on it.

## Functional path tracing

A part can build, fit the bed and have no overlaps while its job is impossible: a sealed channel, a spout filled
solid, a cable route blocked. Trace the function with a probe.

1. Build a probe the size of the thing that must pass (water channel, cable, ball, finger).
2. Place it at the start of the route; sweep or step it along the route with `ctx.slide`, or place several probes along
   the path and run `ctx.interference` on each.
3. Assert zero interference at every step.
4. Break it on purpose (narrow the channel, block the spout) and confirm the check FAILs.

If the route turns, test each straight segment and each junction.

## Stability

For anything that stands, hangs or tips:

- Centre of mass: read it from the built solids (`center_of_mass_mm` in `manifest.json` or `agentcad measure --json`),
  not from your estimate.
- Support polygon: the convex hull of the contact points with the ground in the use pose. The centre of mass projected
  down must be inside it, with a margin.
- Check each loose sub-assembly separately. An assembly can be stable as a whole while one loose stage alone tips.
  Lift each stack out and test it on its own.
- Check the heaviest-loaded and the lightest-loaded cases.

## Negative tests

Every check must have been seen to FAIL once.

1. Write the check.
2. Break the geometry (or the probe) so the requirement is violated.
3. Run `agentcad check`; read the FAIL and confirm the measured number is the one you expected.
4. Restore and confirm PASS.

A check that passes both broken and intact measures nothing. Record the broken-case number in your report.

## Freshness

- Re-run `agentcad check` after every edit.
- Never trust a report older than the model file. Compare timestamps (`report.json` next to `manifest.json`).
- agentcad builds atomically, so a crashed build does not leave stale files that look fresh, but an old report can still
  be on disk. A run that exits 3 produced no result.
- When you see a result you did not produce this turn, rebuild and rerun.

## Reviewing another agent's CAD

Assume the PASS lines are wrong. Re-measure from their exported files:

```bash
agentcad measure part.step --json            # bbox, volume, solids, valid
agentcad interfere a.stl b.stl --json        # overlap as placed
agentcad overhang part.stl --max-deg 45 --json
agentcad wall part.stl --thin 1.2 --json
agentcad fit part.stl --printer generic-220 --json
```

Compare each number with the claim in their report. If they exported STL in the assembled pose, interference is valid;
if in the print pose, check overhang and wall instead. If only parameters were reported, the claim is unverified.

## What renders are for

Look at renders every time: `agentcad render` writes `sheet.png`; `agentcad view` shows the model live. They catch
gross errors: a missing feature, a part on the wrong side, a lid facing the wrong way. They are not evidence for
thickness, clearance or overlap. A clean render hid a 50 cm3 overlap, a sealed channel and a 1.5 mm wall.

## Reporting

- Quote measured numbers with units: "side play 0.25 mm, wall p1 1.9 mm, unsupported overhang 0 mm2".
- Say which checks you saw fail and how.
- State the part count and which parts are printed.
- List what remains a bench check: force of a snap, real tolerances of the printer, material behaviour, fit with
  third-party parts that were not available as models.
