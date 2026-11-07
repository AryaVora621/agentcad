# Blank agentcad project

`model.py` defines the parts in `parts()` and your requirements in `checks(ctx)`. `agentcad.toml` sets the printer
and the thresholds of the built-in gates.

```bash
agentcad check          # build, run every gate and your checks
agentcad check --json   # the same, as JSON for an agent
agentcad render         # PNG views in build/renders/
```

Replace the cube with your part, then turn each requirement into a `ctx.expect(...)` measurement.
