# Design for printing (FDM)

Every number in this file is a starting point. Confirm it with a test coupon on the target printer, nozzle and
material before committing a design to it. A coupon is a small print with a row of holes, pegs or walls at stepped
sizes; it takes minutes and replaces guessing.

The numbers assume a 0.4 mm nozzle and 0.2 mm layers unless stated.

## Overhangs

- Default limit: a downward face tilted up to 45 degrees from vertical prints without support. The agentcad
  `overhang` gate uses this angle (`max_overhang_deg = 45`) and measures the area of faces that exceed it in the print
  frame, ignoring faces that sit on the bed and downward faces centred below `first_layer_mm` (default 0.3 mm; 0
  disables), which print as part of the first layers. The exempt area is reported as `first_layer_area_mm2`.
- `overhang_allow_mm2` is the unsupported area you accept per part. A few tens of mm2 of rough underside is usually
  fine; hundreds are not. Set it deliberately and keep it low.
- Better fix than support: reorient the part (`Part.print`), split it, or change the shape so the underside is a
  45 degree chamfer.
- Chamfer downward-facing edges instead of filleting them. A fillet turns from vertical to horizontal and always
  includes a region steeper than the limit; a 45 degree chamfer does not.

## Bridges

Short horizontal spans between two supports are usually fine. As a starting point, spans up to about 10 mm print
without tuning on most printers with good cooling. Treat longer spans as a risk: they sag, and the underside gets
rough. For a long span, add a mid support rib, split the part, or accept support for that region.

## Horizontal holes

A circular hole with an axis parallel to the bed has a ceiling that prints poorly. Give the top a teardrop or diamond
profile (point up, sides at 45 degrees) so it prints without support. The hole is then slightly taller than wide;
if the hole must be round, print it vertical or use a sacrificial one-layer bridge and drill it.

## Fits

Clearance per side for a 0.4 mm nozzle, as a starting range:

| Fit | Per side | Notes |
|---|---|---|
| Press | about 0.0 to 0.1 mm | needs force; hole shrinks after printing, test it |
| Snug / sliding | about 0.15 to 0.3 mm | moves by hand without rattling |
| Loose | about 0.4 mm and up | free movement, visible play |

Holes print smaller than designed and pegs print larger. Small holes (under about 5 mm) are the worst. Measure the
printed coupon and put the correction in one named constant in `model.py`, not scattered through the geometry. Verify
the result with `ctx.play`, which reports the clearance of the built solids.

## Walls

- Minimum wall: at least two perimeters, so about 2 x the line width (about 0.8 to 0.9 mm for a 0.4 mm nozzle). For
  functional parts, 1.2 mm or more is a better starting point.
- The agentcad `min_wall` gate compares the 1st percentile (`p1`) of the measured wall thickness to `min_wall_mm`,
  not the median. The median hides a thin neck or a facet cut; p1 catches the thin 1 percent of the surface that
  actually breaks. Use `agentcad wall FILE --thin MM` to see where the thin samples are.
- Walls that are not a multiple of the line width leave gaps or force thin-wall fill. Prefer multiples of the line
  width when the wall is critical.

## Elephant foot

The first layers squash outward and the part grows at the bed. Chamfer bottom edges by about 0.4 to 0.6 mm (the `overhang` gate does not count a chamfer
that small, see `first_layer_mm`). This
matters for fits near the bed: a lid lip or a pocket there will bind without the chamfer.

## Layers, orientation and strength

Parts are weakest across layers (z in the print frame) and strongest along them.

- Orient loads along the layers. A hook, clip or lever loaded across layers will delaminate.
- Put screw pilots perpendicular to the layers (axis vertical in the print frame) where you can: they print round and
  close to size. Make the boss wide, with at least two perimeters all round, because the thread's pull-out load then
  acts across layers. For a screw that carries a large pull-out load, use a through bolt and nut instead.
- Counterbores and countersinks depend on print direction: a counterbore facing up prints cleanly; one facing down
  needs a bridge or a step-down chamfer.
- Round features (shafts, pins) print best with the axis vertical; round holes print best vertical too.

## Screws in plastic

Starting points for self-tapping screws into printed plastic:

- Pilot diameter about 80 to 90 percent of the nominal screw diameter. For M4 that is about 3.5 to 3.7 mm.
- Engagement of at least 2 x the screw diameter (8 mm for M4).
- At least 2 x the diameter from the hole centre to an outer face (8 mm for M4), or the boss splits.
- Add a boss with generous wall (at least two perimeters all round, more is better) rather than a hole in a thin wall.
- Alternatives that survive repeated assembly: a through bolt with a nut (captured in a hex pocket), or heat-set
  inserts if the process allows. Do not rely on a self-tapping screw for a part that is opened often.

Check the design with a probe cylinder at the real screw size (`ctx.interference` equals 0 and `ctx.distance` in a
band) and with `min_wall` for the boss wall.

## Snap fits and flexures

- Deflect along the layers, not across them. A beam printed with layers along its length bends; one printed with layers
  across its length snaps at the first layer line.
- Use a generous root radius where the beam meets the body. A sharp corner concentrates stress and cracks.
- Keep the strain low; printed materials differ widely (PETG and nylon flex, PLA is brittle). Plan on a test print.
- A latch overlaps its catch only at the barb along its travel. Test that with `ctx.slide`.

## Bed contact and warping

- Large flat footprints stick well but warp at corners in some materials. Round the corners of large plates.
- A small contact area tips or detaches. agentcad warns (`bed_contact`) when the area on the bed is under 1 percent of
  the footprint or under 20 mm2. Add a brim in the slicer, or add a wider first-layer foot to the model.
- Materials differ in warping: ABS, ASA and nylon warp more than PLA and PETG. Plan enclosure and cooling to match.
- Sharp thin features near the bed corners are the first to lift.

## Splitting parts

Split a part when it exceeds the bed (`bed_fit` fails), or when splitting removes supports or lets each half print in
its best orientation. Put the split where it also hides a joint: a dovetail, a tongue-and-groove with 0.15 to 0.3 mm
clearance, or a bolted flange. Add alignment features so the halves cannot be assembled wrong.

## Keep it simple

- Fewest parts that work. Every extra part is an extra tolerance stack.
- One fastener size across the design. One driver, one drill.
- No blind joints: if you cannot reach the fastener, redesign.
- Assemble with two hands and stock tools. If it needs a jig, include the jig.
- Mark parts that are not printed with `printed=False` so the print gates skip them.
