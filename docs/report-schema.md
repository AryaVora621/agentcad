# JSON schemas

Every JSON document has a `schema` field with a version. Fields are only added within a major version.
Floats are rounded to 4 decimal places; NaN and infinity become `null`.

## agentcad.report/1

Printed by `check`, `measure`, `interfere`, `overhang`, `wall`, `fit` and `doctor`, and written to
`build/report.json` by `check`.

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | `"agentcad.report/1"` |
| `tool` | string | the command: `check`, `measure`, `interfere`, `overhang`, `wall`, `fit`, `doctor` |
| `project` | string or null | project name, null for file commands |
| `status` | string | `FAIL` if any check is FAIL or ERROR, else `WARN` if any WARN, else `PASS` |
| `exit_code` | int | 0, 1 (a FAIL) or 3 (only ERRORs) |
| `counts` | object | number of checks per status: `pass`, `warn`, `fail`, `skip`, `error` |
| `checks` | array | the checks, below |
| `meta` | object | versions, printer, build folder, `elapsed_s` |

Each check:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | built-in gate id, or the id given to `ctx.expect`, or the name of the project check function that raised |
| `part` | string or null | part name, `a+b` for a pair, null when project-wide |
| `status` | string | `PASS`, `WARN`, `FAIL`, `SKIP` or `ERROR` |
| `summary` | string | one line a person or agent can act on |
| `measured` | object | numbers with unit-suffixed keys (`area_mm2`, `value_mm`) |
| `limit` | object | the thresholds used (`max_area_mm2`, `min_mm`) |
| `where` | array | up to five worst locations: `centroid_mm`, `area_mm2` or `volume_mm3`, `z_mm` |
| `hint` | string, optional | how to fix it; present only on a FAIL or WARN that knows a fix (every built-in gate does, and `ctx.expect(..., hint=...)` on a failure), at most about 220 characters, naming the numbers and the `Part` or config field to change |

ERROR means the check itself could not run (the project check raised, a gate hit an exception, or the model failed
to build). It carries no `measured` values.

Example, from `agentcad check examples/bracket --json` with the checks trimmed to two:

```json
{
  "schema": "agentcad.report/1",
  "tool": "check",
  "project": "bracket",
  "status": "PASS",
  "exit_code": 0,
  "counts": {"pass": 11, "warn": 0, "fail": 0, "skip": 0, "error": 0},
  "checks": [
    {
      "id": "overhang",
      "part": "bracket",
      "status": "PASS",
      "summary": "0.0 mm2 unsupported at 45 deg (allowed 25)",
      "measured": {"area_mm2": 0.0, "regions": 0},
      "limit": {"max_area_mm2": 25, "max_overhang_deg": 45},
      "where": []
    },
    {
      "id": "m4_clearance_1",
      "part": "bracket",
      "status": "PASS",
      "summary": "0.2 mm (within min 0.1 and max 0.4)",
      "measured": {"value_mm": 0.2},
      "limit": {"min_mm": 0.1, "max_mm": 0.4},
      "where": []
    }
  ],
  "meta": {
    "agentcad": "0.1.0",
    "cadquery": "2.8.0",
    "printer": "generic-220",
    "bed_mm": [220, 220, 250],
    "frames": {"printability": "print", "fit": "use"},
    "build": ".../bracket/build",
    "elapsed_s": 1.58
  }
}
```

### Brief view

`check --json --brief` prints the same schema id with an extra `"view": "brief"` and compact separators, for
agents that re-read the report after every edit. The full view is unchanged (no `view` or `passed` key).

| Field | Meaning |
|---|---|
| `view` | `"brief"` (absent in the full view) |
| `schema`, `tool`, `project`, `status`, `exit_code`, `counts` | as in the full view |
| `checks` | only checks that are not PASS (FAIL, ERROR, WARN, SKIP); `measured`, `limit` and `where` are left out when empty |
| `passed` | the passing checks grouped by id: `{"valid": ["left", "right"], "interference": ["left+right"]}`; `"-"` stands for a check with no part |
| `meta` | only `elapsed_s` and `build` |

`examples/broken` is about 18 KB in the full view and about 3 KB in the brief view; an all-pass project such as
`examples/bracket` is under 1 KB. `check --brief` without `--json` prints one row per non-PASS check with an
indented `  hint:` line under each one that has a hint, then `N passed: valid x6, overhang x3, ...` and the totals line.

A failing check from `examples/broken`, with its location and fix hint:

```json
{
  "id": "overhang",
  "part": "tee",
  "status": "FAIL",
  "summary": "300.0 mm2 unsupported > 25 allowed; worst 300.0 mm2 at z 25.0..25.0",
  "measured": {"area_mm2": 300.0, "regions": 1},
  "limit": {"max_area_mm2": 25, "max_overhang_deg": 45},
  "where": [{"centroid_mm": [0.0, 0.0, 25.0], "area_mm2": 300.0, "z_mm": [25.0, 25.0]}],
  "hint": "rotate it in Part.print so a large flat face sits on the bed, or turn the worst face (where[0], 300 mm2 at z 25.0) into a 45 degree chamfer; raise checks.overhang_allow_mm2 only if supports are acceptable"
}
```

and the check that raised:

```json
{
  "id": "check_boom",
  "part": null,
  "status": "ERROR",
  "summary": "raised RuntimeError: deliberate failure to prove that a raising check becomes ERROR",
  "measured": {},
  "limit": {},
  "where": []
}
```

### Built-in check ids

| id | per | Status |
|---|---|---|
| `valid` | part | FAIL when the shape is not a valid solid with positive volume |
| `single_solid` | part | WARN when the part is more than one disconnected solid |
| `watertight` | printed part | FAIL when the print STL is not watertight or has inconsistent winding |
| `bed_fit` | printed part | FAIL when the print-frame bounding box does not fit the printer in either XY orientation |
| `bed_contact` | printed part | WARN under 1 % of the footprint or under 20 mm2 |
| `overhang` | printed part | FAIL above `checks.overhang_allow_mm2`; faces centred below `checks.first_layer_mm` (default 0.3) are exempt and counted in `first_layer_area_mm2` |
| `min_wall` | printed part | FAIL when p1 is below `checks.min_wall_mm`; one SKIP row when the gate is off |
| `interference` | part pair | FAIL above `checks.interference_tol_mm3` unless the pair is in `allow_overlap` |

## agentcad.manifest/1

Printed by `build --json` and written to `build/manifest.json`.

| Field | Meaning |
|---|---|
| `schema` | `"agentcad.manifest/1"` |
| `project`, `agentcad_version`, `created` | name, version, UTC timestamp |
| `export` | `tolerance` (mm) and `angular_tolerance` (rad) used for the STL files |
| `printer` | `name`, `bed_xyz`, `nozzle_mm`, `notes` |
| `parts[]` | one entry per part, below |
| `files` | sha256 of files not tied to one part (`assembly.step`) |
| `sources` | sha256 of `agentcad.toml` and every project `.py`; `check --no-build` uses it to detect a stale build |

Each part: `name`, `qty`, `printed`, `material`, `color`, `volume_mm3` and `area_mm2` (measured on the model-frame
solid), `bbox` (`model`, `use`, `print`, each with `min_mm`, `max_mm`, `size_mm`), `center_of_mass_mm` (use frame),
`solids`, `valid`, `triangles` (of the print STL) and `sha256` per exported file.

Example, from `examples/bracket` (the `model` and `use` bounding boxes are left out here):

```json
{
  "schema": "agentcad.manifest/1",
  "project": "bracket",
  "agentcad_version": "0.1.0",
  "created": "2026-11-07T02:12:12+00:00",
  "export": {"tolerance": 0.02, "angular_tolerance": 0.05},
  "printer": {
    "name": "generic-220",
    "bed_xyz": [220, 220, 250],
    "nozzle_mm": 0.4,
    "notes": "Generic build envelope; verify your machine's usable area."
  },
  "parts": [
    {
      "name": "bracket",
      "qty": 1,
      "printed": true,
      "material": "PETG",
      "color": "#4f8cff",
      "volume_mm3": 23347.9469,
      "area_mm2": 10875.4713,
      "bbox": {
        "print": {"min_mm": [-30.0, -20.0, 0.0], "max_mm": [30.0, 20.0, 40.0], "size_mm": [60.0, 40.0, 40.0]}
      },
      "center_of_mass_mm": [0.0, 8.2791, 11.9584],
      "solids": 1,
      "valid": true,
      "triangles": 2056,
      "sha256": {
        "step/bracket.step": "830c53f2...",
        "stl/bracket.stl": "c6d78944...",
        "assembly/bracket.stl": "c6d78944..."
      }
    }
  ],
  "files": {"assembly.step": "26462949..."},
  "sources": {"agentcad.toml": "76e6482a...", "model.py": "95f59a87..."}
}
```

## agentcad.diff/1

Printed by `diff --json`:

```json
{
  "schema": "agentcad.diff/1",
  "old": "box-with-lid",
  "new": "box-with-lid",
  "changed": true,
  "parts": {
    "added": [],
    "removed": [],
    "changed": [
      {"name": "lid", "volume_mm3": {"old": 6888.0, "new": 6873.6, "delta": -14.4, "delta_pct": -0.2091}}
    ]
  }
}
```

A changed part can also carry `bbox_model_size_mm`, `bbox_use_size_mm`, `bbox_print_size_mm` (each `{old, new}`),
and `qty`, `printed` or `solids` (each `{old, new}`).

## Other documents

- `agentcad.error/1`: `{"schema", "error", "exit_code"}`, printed instead of a report on a usage, config or build
  error when `--json` is set.
- `agentcad.printers/1`: `{"schema", "printers": [{"name", "bed_mm", "nozzle_mm", "notes"}]}`.
- `agentcad.new/1`: `{"schema", "path", "template", "files"}`.
- `agentcad.render/1`: `{"schema", "out", "files"}`.
