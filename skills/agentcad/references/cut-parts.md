# Designing cut parts (laser, CNC router, waterjet)

A cut part is a sheet cut straight through. Model it as a 3D plate, mark it `Part(..., cut="laser")` (or `"cnc"`,
`"waterjet"`), and let agentcad section it, check the profile and write `build/dxf/<part>.dxf` and
`build/svg/<part>.svg`. Settings live in `[flat]` of `agentcad.toml`; gates and limits are in `docs/flat.md`.

The numbers below are typical starting values from common shop practice, not guarantees. Ask the shop for its kerf and
minimum feature size, cut a test coupon in the real sheet, and put the measured values into `[flat]`.

## Process facts

| | Laser (CO2, diode) | CNC router | Waterjet |
|---|---|---|---|
| Material removed | kerf, about 0.1 to 0.3 mm | the endmill, commonly 3.175 mm (1/8 in) or 6 mm | kerf, about 0.5 to 1.2 mm |
| Inside corner | sharp is fine | radius of at least half the tool diameter | radius about half the kerf |
| Smallest hole | about the sheet thickness | the tool diameter | about the sheet thickness, often more |
| Edge | slight taper, scorched wood or acrylic | clean, tabs or holding needed | taper on thick stock |
| Sheet thickness varies | plywood often 0.2 to 0.4 mm off nominal | same | same |

Measure the real sheet thickness with calipers before you model it. A "3 mm" plywood sheet that is 2.8 mm makes every
tab-and-slot joint loose.

## Rules, and how to check each one

| Rule | How agentcad checks it |
|---|---|
| Only through-cuts: no pockets, steps, chamfers, engraving | `flat_profile` FAILs when volume differs from profile area x thickness by over 1 percent. Move the feature to another part or a 3D print. |
| Part fits the sheet or bed | `sheet_fit` against `[flat].sheet` (usable area, not the nominal sheet; common stock is 1220 x 2440 mm plywood, laser beds 600 x 400 mm and up). Keep the margin the shop needs out of `sheet`. |
| Narrowest web or tab at least `min_feature_mm` (start at the sheet thickness, never under 1 mm) | `min_feature` FAIL; `where` lists each `kind: web` region with `centre_mm` and `size_mm`. |
| No slot narrower than the kerf | `min_feature` FAIL for `kind: slot`. A slot that narrow closes up when cut. |
| Hole diameter at least the sheet thickness | `hole_size` WARN under `min_hole_ratio` x thickness (default 1.0). Use 1.5 for acrylic over 3 mm and for waterjet. |
| CNC inside corners round to at least half the tool diameter | `inner_corners` WARN, with `tool_diameter_mm` set to the real endmill. Measured: a 20 x 20 mm pocket with square corners and a 6 mm tool warns, the same pocket with 3.2 mm corner radii passes. A dogbone (a circle over-cutting the corner) exists to defeat the tool, and the gate does not recognise it (a corner-centred dogbone still warned); judge that WARN from `where` and say in your report that it is deliberate. |
| Kerf-correct fits | See below; check the fit with `ctx.play` or `ctx.interference`. |

Run `agentcad check NAME --brief`. Each flat gate emits one check per cut part, and `inner_corners` only for `cnc`.
Prove a gate can fail on your part: shrink a slot to 0.1 mm or set `tool_diameter_mm` to 8 and see the WARN appear.

## Kerf and fits

A cutter centred on the drawn line removes half a kerf on each side: holes and slots come out wider by the kerf, tabs
and outlines narrower. Two ways to handle it:

1. `compensate_kerf = true` in `[flat]`. The exported DXF and SVG grow the outline by `kerf_mm / 2` and shrink each hole
   by the same amount, so the finished part has the nominal size. The 3D model and every check stay nominal. Set
   `kerf_mm` to the value you measured on a coupon. Use this when the shop does not offset the path.
2. Leave it off and put the allowance into the model (slot width = thickness + clearance + kerf). Use this when the
   shop applies its own kerf offset, otherwise you compensate twice.

Pick one. Never both.

Fits, in nominal terms with kerf compensated (start here, then tune on a coupon):

| Fit | Slot width |
|---|---|
| Loose, slides by hand | thickness + 0.10 to 0.20 mm |
| Snug, holds with a little friction | thickness + 0.00 to 0.05 mm |
| Press fit, needs a mallet or glue-free hold | thickness - 0.05 mm (list the pair in `checks.allow_overlap`) |

Check a fit on the built solids, not on the numbers you typed (verified on a 3 mm tab in a 3.1 mm slot):

```python
def checks(ctx):
    play = ctx.play("wall", "base", direction=(0, 1, 0), max_mm=1.0)   # free travel before the tab touches
    ctx.expect("tab_play_y", 1.0 if play is None else play, min=0.02, max=0.15, part="wall")
    ctx.expect("tab_overlap", ctx.interference("wall", "base"), max=0.01, unit="mm3", part="wall")
```

That gave 0.0506 mm of play and 0 mm3 of overlap. With the slot narrowed by 0.05 mm the same checks FAIL (play 0 mm,
overlap 1.94 mm3), which proves they can. For a designed press fit, assert the overlap instead and allow the pair.

## Tab-and-slot joints

- Place the pieces in their assembled pose with `Part.use`, and lay each one flat for cutting with `Part.print`
  (`Pose(rx=90)` for a plate modelled standing up along XZ). Then `interference` and `ctx.play` measure the joint.
- Tab length at least twice the thickness, tab height equal to the thickness of the mating sheet (a tab taller than
  the sheet sticks out, shorter leaves a gap you can measure with `ctx.distance`).
- Round or chamfer the tab tip a little so it starts into the slot; keep the slot inside webs of at least
  `min_feature_mm` from the edge and from other holes.
- Space tabs along an edge instead of one long tab: a long tab binds when the sheet is slightly off thickness.
- T-slot with a captive nut: the screw goes through the tab's mating sheet into a slot sized for the nut. For an M3
  nut (5.5 mm across flats, 2.4 mm thick) cut a slot about 5.7 mm wide and 2.6 mm tall, and a hole of 3.2 mm for the
  screw. Probe it: build the nut as a hexagonal prism and assert `ctx.interference` 0 and a `ctx.distance` band.
- Screw holes: 3.2 mm for M3, 4.2 mm for M4 clearance. `hole_size` warns under the thickness, so a 3.2 mm hole in
  3 mm stock passes.

## Sheet use and stock

- One part per file: agentcad does not nest several parts on a sheet and does not lay `qty` copies out. Check each
  part against `sheet` and let the shop's software nest.
- Leave 5 to 10 mm between parts and from the sheet edge when you nest by hand.
- Grain and veneer direction matter for wood: keep thin necks across the grain at least twice `min_feature_mm`.
- For CNC, plan holding: tabs (small uncut bridges) or vacuum. A part with no web to the sheet falls out at the end of
  the cut. agentcad does not model tabs; say what you assume.
- A part that needs a pocket, bend or thread is not a cut part. Split it: the cut plate here, the 3D-printed or
  bought piece as a separate `Part` (as `examples/flat_panel` does with its bracket).

## Report with your result

Quote, with units: the sheet thickness you assumed, `kerf_mm` and where it came from, the five flat gate results for
every cut part, the measured play or overlap of each joint, and what stays a bench check (real kerf on the real
sheet, press-fit force, edge scorch).
