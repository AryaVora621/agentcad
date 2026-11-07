# Box with lid

A 60 x 40 x 30 mm open box (2 mm walls) and a lid whose 6 mm lip drops inside with 0.25 mm clearance per side. The
lid prints upside down (`print=Pose(rx=180)`) so the plate lies on the bed.

`checks(ctx)` proves the lid lifts straight off and measures the side play along X and Y (0.15 to 0.40 mm). Change
`CLEARANCE` in `model.py` and `agentcad check` shows which requirement breaks.

```bash
agentcad check
agentcad render
```
