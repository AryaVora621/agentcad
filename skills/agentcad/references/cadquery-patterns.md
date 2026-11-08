# CadQuery patterns and traps (CadQuery 2.8)

Practical notes for writing `model.py` so agentcad can build and measure it. Everything here is plain CadQuery.

## Workplane versus Shape

`cq.Workplane` is a chain of operations with a stack of objects. `cq.Shape` (`cq.Solid`, `cq.Compound`) is the
underlying OpenCASCADE shape. `Part.shape` accepts either.

- `wp.val()` returns only the first object on the stack. `wp.vals()` returns all of them.
- To combine several objects into one shape, use `cq.Compound.makeCompound(wp.vals())`.
- After a boolean (`union`, `cut`, `intersect`) the stack holds one fused result. After `.faces()`, `.edges()` or
  `.vertices()` it holds selected sub-shapes, not the solid; chain a further operation or go back with `.end()`.
- Build each part in a function that returns a `Workplane`. Keep parameters as named constants at the top of the file.

```python
import cadquery as cq

WALL = 2.0


def make_box() -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .box(60, 40, 30, centered=(True, True, False))
        .faces(">Z")
        .shell(-WALL)
    )
```

## Placement: local frame plus `Part.use`

Model each part in a convenient local frame (origin at its natural datum, axis along its natural direction) and place
it in the assembly with `Part.use=Pose(...)`. Do not bake assembly positions into the geometry with `.translate` or
`.rotate`. Two reasons: the print rotation (`Part.print`) is then independent of the assembly pose, and agentcad can
measure each frame separately.

`Pose` rotates about X, then Y, then Z (extrinsic, about the part's own origin), then translates. If a rotation puts a
part somewhere unexpected, check the bounding box with `agentcad measure` rather than reasoning about it.

## Selectors

- `">Z"` the face or edge furthest in +Z, `"<X"` furthest in -X.
- `"|Z"` edges or faces parallel to Z; `"#Z"` perpendicular to Z.
- `.faces(">Z")` selects faces, `.edges("|Z")` selects edges. Choose the type before the direction.
- Combine with `and`, `or`, `not`: `.edges("|Z and >X")`.
- Check what a selector picked before you operate on it: `len(wp.edges("|Z").vals())`.

Fillet or chamfer only what you mean: select the edges explicitly, then operate.

```python
body = cq.Workplane("XY").box(30, 20, 10).edges("|Z").fillet(3.0)   # vertical corners only
body = body.edges("<Z").chamfer(0.5)                                  # bottom edges, elephant foot
```

## Fillet and chamfer failures

- A fillet larger than the adjacent wall or face fails, often with a StdFail error. Use a smaller radius.
- Fillet before booleans when you can: filleting a simple solid is robust, filleting after many cuts is fragile.
- Fillet and chamfer edges in separate steps; mixing them on touching edges often fails.
- Fillets on downward-facing edges produce overhangs; use chamfers there (see design-for-printing).

## Validity after every boolean

A boolean can silently return an empty or invalid result (coplanar faces, tangent surfaces, tiny slivers).

```python
result = a.union(b)
shape = result.val()
assert shape.isValid(), "union produced an invalid solid"
assert shape.Volume() > 0
```

agentcad reports this as the `valid` gate, but catching it near the cause is faster. If a boolean fails, move one
operand by a small amount (0.01 mm) so faces are not coplanar, or overlap the cutter past the face it cuts through
instead of ending exactly on it.

## Shells

`.shell(-t)` hollows a solid and removes the selected face, with a negative value for wall thickness inward.

```python
open_box = cq.Workplane("XY").box(60, 40, 30, centered=(True, True, False)).faces(">Z").shell(-2.0)
```

The shell thickness must be smaller than the smallest feature.
Shelling fails on complex shapes; shell the simple body first and add features after.

## Revolve and loft

```python
profile = cq.Workplane("XZ").polyline([(0, 0), (20, 0), (20, 10), (10, 25), (0, 25)]).close()
vase = profile.revolve(360, (0, 0, 0), (0, 1, 0))
```

Revolved and lofted surfaces have seams. When they are exported to STL, a coarse tolerance cuts facets through thin
walls. Keep `[export]` at `tolerance = 0.02` and `angular_tolerance = 0.05`, and check the wall gate on the exported
mesh. Lofts between very different profiles can twist; keep the same number of segments in each section.

### Watertight failures at poles and tangent seams

A revolved or spherical cavity can export a mesh with an open edge, and `watertight` FAILs even though no face is
coincident (zero-thickness-face advice does not apply). The usual causes:

- **Pole.** A revolve profile that ends exactly on the axis, or a sphere cut whose pole sits on a face, leaves a
  degenerate vertex. Stop the profile off the axis with a small flat (a 3 to 6 mm flat floor), or close it with a
  line to the axis and revolve once.
- **Tangent seam.** A sphere meeting a cylinder at its equator, or a fillet meeting a face tangentially, is a seam
  the mesher splits badly. Shift one by 0.01 to 0.05 mm, or end the cylinder a little before the equator and let
  the sphere continue below it.
- **Revolve on the wrong plane.** Revolving on "XY" about the Y axis gives a sideways cavity (a huge overhang and
  interference). Draw the profile on "XZ" and revolve about its local Y axis (`axisStart=(0, 0, 0)`, `axisEnd=(0, 1, 0)`),
  which is the global Z axis; check the result with a section.

To find the open edge, load `build/stl/<part>.stl` with trimesh (if installed) and list `mesh.edges` not shared by two faces
(`trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)`). Re-run `agentcad check` for the full rebuild after
each fix.

## Real arcs and circles

Use `circle`, `ellipse`, `threePointArc`, `radiusArc`, `fillet2D`, not polylines that approximate curves. A polygon
with 32 sides changes the measured diameter, makes press fits unpredictable and adds facets to the export. If a curve
must be a polyline (an imported profile), use enough segments that the sagitta (the deviation from a true circle) is
below 0.02 mm.

## Holes

```python
plate = (
    cq.Workplane("XY")
    .box(40, 40, 6)
    .faces(">Z").workplane()
    .pushPoints([(-10, 0), (10, 0)])
    .hole(4.2)                       # through hole, M4 clearance as a starting point
)
bossed = (
    cq.Workplane("XY")
    .box(40, 40, 6)
    .faces(">Z").workplane()
    .cboreHole(4.5, 8.0, 3.0)        # diameter, counterbore diameter, counterbore depth
)
sunk = (
    cq.Workplane("XY")
    .box(40, 40, 6)
    .faces(">Z").workplane()
    .cskHole(4.5, 9.0, 90)           # diameter, countersink diameter, angle
)
```

Counterbores and countersinks depend on print direction. Facing up they print clean; facing down they print a bridge
over the counterbore. Decide the print orientation first, then place them.

`.hole` works from the current workplane origin or from `pushPoints`. Select the face with `faces()` before
`.workplane()` or the hole lands on the wrong plane.

## Text and emboss

`Workplane.text` works, but it is slow, depends on installed fonts and creates many small faces that make booleans
fragile. Use it sparingly, late in the model, on a flat face, with a size above about 5 mm and a depth of at least
0.6 mm (the thinnest feature a 0.4 mm nozzle prints reliably as a starting point). Verify the result with
`valid` and the wall gate.

## Exporting

agentcad exports for you (`agentcad build`). If you export yourself with `cq.exporters.export`, pass explicit
tolerances for STL:

```python
cq.exporters.export(shape, "out.stl", tolerance=0.02, angularTolerance=0.05)
cq.exporters.export(shape, "out.step")
```

Do not rely on the default tolerance; it is coarse enough to cut thin walls.

## Probe solids

Probes are stand-ins for the mating thing, built in the use frame and passed to `ctx.interference`, `ctx.distance`,
`ctx.slide` and `ctx.play`.

```python
screw = cq.Workplane("XY").circle(2.0).extrude(30)                 # 4.0 mm shank, axis Z
screw = screw.translate((10, 0, -5))                               # place at the intended hole
ball = cq.Workplane("XY").sphere(27.0).translate((0, 0, 40))       # 54 mm ball, radius 27
```

Build them from the requirement (the real screw, the real ball), not from the parameter that sized the hole.
