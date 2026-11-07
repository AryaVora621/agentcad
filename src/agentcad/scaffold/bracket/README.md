# L-bracket

A 60 x 40 x 5 mm base with a 40 mm upright, a triangular gusset and two 4.4 mm holes for M4 screws. It prints as-is,
base on the bed.

`checks(ctx)` builds a 4.0 mm M4 stand-in on each hole axis and measures the radial clearance (0.1 to 0.4 mm) and the
overlap (none). Move a hole in `model.py` and `agentcad check` reports which screw no longer fits.

```bash
agentcad check
agentcad render
```
