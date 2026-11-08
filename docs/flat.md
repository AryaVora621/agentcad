# Flat parts: laser, CNC router and waterjet

A flat part is a sheet cut straight through. You still model it as a 3D plate; agentcad sections it, checks the
profile against what the cutter can do and writes a DXF and an SVG in millimetres.

## Model a cut part

```python
import cadquery as cq
from agentcad import Part

def parts():
    panel = (cq.Workplane("XY").box(200, 120, 3, centered=(True, True, False))
             .edges("|Z").fillet(6)
             .faces(">Z").workplane().pushPoints([(-90, -50), (90, -50)]).hole(3.2))
    return [Part("panel", panel, cut="laser", material="3 mm birch plywood")]
```

`cut` is `"laser"`, `"cnc"`, `"waterjet"` or left out. Anything else is a config error (exit 2).

The print frame of a cut part is its cutting frame: the sheet lies on `z = 0` and the thickness runs along `+z`.
If you modelled the plate standing up, give it `print=Pose(rx=90)` (or whichever rotation lays it flat). Thickness is
the measured z extent of the part in that frame. Parts with `cut` set skip the 3D-printing gates (`watertight`,
`bed_fit`, `bed_contact`, `overhang`, `min_wall`); `valid`, `single_solid` and `interference` still run.

## The `[flat]` section of agentcad.toml

| Key | Default | Meaning |
|---|---|---|
| `sheet` | `[600, 400]` | Usable sheet size `[width, height]` in mm for `sheet_fit` |
| `kerf_mm` | `0.15` | Width of material the cutter removes; slots narrower than this close up. 0 disables the slot half of `min_feature` |
| `min_feature_mm` | `1.0` | Narrowest web or tab that survives cutting. 0 disables the web half of `min_feature` |
| `tool_diameter_mm` | `3.175` | CNC endmill diameter (1/8 inch) for `inner_corners`. 0 disables the gate |
| `min_hole_ratio` | `1.0` | A circular hole narrower than this times the thickness warns. 0 disables `hole_size` |
| `compensate_kerf` | `false` | Export the outline grown by `kerf_mm / 2` and the holes shrunk by it |

Unknown keys and bad values are config errors (exit 2) with a one-line message.

## Gates

Each gate emits one check per cut part, measured on a section of the built solid at half the thickness. `where`
entries use sheet coordinates (the outline's lower left corner is 0, 0), the same as the DXF.

| Gate | Fails or warns when |
|---|---|
| `flat_profile` | FAIL when `abs(volume - profile_area * thickness) / volume` exceeds 1 percent, or the sections at 10 and 90 percent of the thickness differ from the mid section. Cutters only go straight through; move pockets, steps, chamfers and engraving to another part or a 3D print. |
| `sheet_fit` | FAIL when the outline's bounding box fits `[flat].sheet` neither as is nor turned 90 degrees. The check says which. |
| `min_feature` | FAIL when a web or tab is narrower than `min_feature_mm` (a morphological opening by half of it removes area) or a slot is narrower than `kerf_mm` (a closing by half of it adds area). `where` lists up to 10 regions with `kind` (`web` or `slot`), `centre_mm`, `size_mm` and `area_mm2`. |
| `inner_corners` | CNC parts only. WARN for inside corners and slots a `tool_diameter_mm` endmill cannot reach (a closing by the tool radius adds area). Add dogbones or give corners a radius of at least half the tool diameter. |
| `hole_size` | WARN for circular holes narrower than `min_hole_ratio * thickness`. Slots and non-circular holes are ignored. |

Area tolerances: a sharp 90 degree corner always loses about `0.21 r^2` to a rounded opening or closing, so
`min_feature` ignores regions under `max(0.01, 0.4 r^2)` mm2 (r is half the feature size). A 0.2 x 5 mm sliver is
1 mm2 and is always caught. `inner_corners` ignores regions under 0.02 mm2, which also covers a corner radius a
hair under the tool radius. `flat_profile` allows a section mismatch of `0.05 mm2 + 0.01 percent of the area`.

## Outputs

`agentcad build` writes, per cut part, next to the existing `step/`, `stl/` and `assembly/`:

- `build/dxf/<part>.dxf`: R2010, `$INSUNITS = 4` (mm), layer `CUT`. Holes that are full circles are `CIRCLE`
  entities; every other loop is one closed `LWPOLYLINE` with bulges for arcs. Splines and ellipses are sampled to
  0.005 mm.
- `build/svg/<part>.svg`: the same loops as paths, sized in mm, y pointing down.
- A `flat` record in the part's manifest entry: `thickness_mm`, `profile_area_mm2`, `cut_length_mm` (total length of
  the exported loops), `holes`, `outline_mm`, `kerf_compensated`, `kerf_mm` and `files` with the SHA-256 of both
  files. The same hashes are in the part's `sha256` map, so `check --no-build` notices an edited DXF.

With `compensate_kerf = true` the exported outline is grown by `kerf_mm / 2` per side and each hole shrunk by the
same amount, so the finished part has the nominal size. The exported geometry is re-anchored so its lower left
corner is again at 0, 0; `outline_mm` in the manifest stays nominal. A hole or notch that the compensation would
collapse is a build error (exit 3) that names where.

## Limits

- Through-cuts only. There is no engraving, scoring, bending or pocketing.
- One part per file. There is no nesting of several parts on one sheet and `qty` does not lay copies out.
- Material, kerf and tool settings are project-wide, not per part.
- The thin-feature gates work on the section as polygons (arcs sampled to 0.005 mm), so they measure the shape that
  would be cut but not the cutter path or the order of cuts. Sharp tips under about 75 degrees count as thin.
