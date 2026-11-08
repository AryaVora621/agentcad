# CLI reference

Every command takes `--json` (except `view` and `mcp`) and then prints exactly one JSON document on stdout. Without it the
output is a compact table. Logs and errors go to stderr.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every check passed. WARN is allowed. |
| 1 | At least one check FAILed. |
| 2 | Usage or configuration error: bad flags, unknown `agentcad.toml` key, missing file, `new` into a non-empty folder. |
| 3 | The model code raised or produced no valid solid (`check` reports it as an ERROR check). |

With `--json`, a code 2 or 3 failure that is not a report prints
`{"schema": "agentcad.error/1", "error": "...", "exit_code": N}` on stdout and the message on stderr.

A project argument is a folder that holds `agentcad.toml` (or the file itself) and defaults to the current folder.
File arguments take STEP (`.step`, `.stp`) or mesh files (`.stl`, `.obj`, `.ply`, `.3mf`, `.glb`).
The JSON shapes are in [report-schema.md](report-schema.md).

## agentcad new

```
agentcad new DIR [--template blank|bracket|box-with-lid] [--json]
```

Copies a template into `DIR`. The default is `blank`. A `DIR` that exists and is not empty is refused (exit 2).

```
$ agentcad new demo --template bracket
created demo from template 'bracket': README.md, agentcad.toml, model.py
next: cd demo && agentcad check
```

## agentcad build

```
agentcad build [PROJECT] [--out DIR] [--json]
```

Exports `step/`, `stl/` (print frame), `assembly/` (use frame), `assembly.step` and `manifest.json` into
`PROJECT/build` or `--out`. The build goes through `build/.tmp-<pid>` and replaces the old files only after every
export succeeded, so a failed build leaves the previous one intact. Other files in `build/` (renders, `report.json`)
are kept. `--json` prints the manifest.

```
$ agentcad build lid
box: 15552.0 mm3, print 60.0 x 40.0 x 30.0 mm, 28 triangles
lid: 6888.0 mm3, print 60.0 x 40.0 x 8.0 mm, 44 triangles
built 2 part(s) into /path/to/lid/build
```

### Cut parts

A part with `cut="laser"`, `"cnc"` or `"waterjet"` also gets its cutting profile exported, next to `step/`, `stl/` and
`assembly/`:

- `build/dxf/<part>.dxf`: R2010, millimetres, layer `CUT`; full-circle holes are `CIRCLE` entities, every other loop
  is one closed `LWPOLYLINE`.
- `build/svg/<part>.svg`: the same loops as paths, sized in mm.
- A `flat` record in the part's manifest entry: `thickness_mm`, `profile_area_mm2`, `cut_length_mm`, `holes`,
  `outline_mm`, `kerf_compensated`, `kerf_mm` and `files` (SHA-256 of the DXF and SVG). The same hashes are in the
  part's `sha256` map, so `check --no-build` notices an edited file.

From `examples/flat_panel`: `thickness_mm` 3.0, `profile_area_mm2` 23584.6531, `cut_length_mm` 794.8761, `holes` 5.
Settings (`[flat]`, kerf compensation) and the gates are in [flat.md](flat.md).

## agentcad check

```
agentcad check [PROJECT] [--json] [--only ID,ID] [--no-build] [--brief]
```

Builds the project, runs the built-in gates, then the project's own checks, and writes the report to
`build/report.json` as well as printing it.

- `--only` runs just the listed check ids. Built-in ids: `valid`, `single_solid`, `watertight`, `bed_fit`,
  `bed_contact`, `overhang`, `min_wall`, `interference`, and for cut parts `flat_profile`, `sheet_fit`,
  `min_feature`, `inner_corners`, `hole_size`. Project check ids are the ones passed to `ctx.expect`.
  An id that matches nothing is exit 2.
- `--no-build` reuses `build/` only if the sources and every exported file still match `manifest.json`; otherwise
  it exits 2 and tells you to build.

- `--brief` prints only the checks that are not PASS, a `hint` for each FAIL or WARN, and the passing checks
  grouped by id. See below.

Project checks are `checks(ctx)` and any module-level function named `check_*`; each runs in isolation, and one that
raises becomes an ERROR check while the rest still run.

```
$ agentcad check lid
PASS   valid           box      valid solid, 15552.0 mm3
...
PASS   interference    box+lid  overlap 0.000 mm3
PASS   lid_lifts_free  lid      0 mm3 (within max 0.001)
PASS   lid_play_x      lid      0.2506 mm (within min 0.15 and max 0.4)
PASS   lid_play_y      lid      0.2506 mm (within min 0.15 and max 0.4)
16 pass, 0 warn, 0 fail, 1 skip
```

A failing run, from `examples/broken`, exit code 1:

```
$ agentcad check bad --only overhang,bed_fit,interference,min_wall
FAIL   bed_fit       long_bar           300.0 x 10.0 x 5.0 mm does not fit generic-220 220x220x250 with 2 mm margin
FAIL   overhang      tee                300.0 mm2 unsupported > 25 allowed; worst 300.0 mm2 at z 25.0..25.0
FAIL   min_wall      thin_cup           p1 0.59 mm < 1.2 mm; 95.4% of surface thin, z 0.0..20.0
FAIL   interference  left+right         overlap 4000.0 mm3 > 0.01
...
21 pass, 0 warn, 4 fail
```

### check --brief

Made for an agent that re-reads the report after every edit. With `--json` the document keeps the schema id
`agentcad.report/1`, adds `"view": "brief"`, uses compact separators, lists only checks that are not PASS, and groups
the passing checks by id in `passed`. Measured on `examples/broken`: 17,895 bytes in the full view, 3,144 bytes in the
brief view; on `examples/bracket` the brief view is 588 bytes. Field details are in
[report-schema.md](report-schema.md#brief-view). Use the full report when you need every `measured` and `limit`
value, or the numbers of a passing check.

Without `--json` it prints one row per non-PASS check, an indented `hint:` line under each one that has a hint, then
the passing checks grouped by id and the totals line. The exit code is the same as without `--brief`.

```
$ agentcad check bad --brief
FAIL   min_wall        thin_cup    p1 0.59 mm < 1.2 mm; 95.4% of surface thin, z 0.0..20.0
  hint: thicken the thin walls to at least 1.2 mm (thinnest 0.59 mm, near z 0.0..20.0); lower checks.min_wall_mm only if the nozzle can print the thinner wall
...
42 passed: valid x5, single_solid x5, watertight x5, bed_fit x4, bed_contact x5, overhang x4, min_wall x4, interference x9, tee_head_area x1
42 pass, 0 warn, 5 fail, 1 error
```

## agentcad measure

```
agentcad measure FILE... [--json]
```

Bounding box, volume, area, centre of mass and solid count of each file. A mesh that is not closed gets a WARN
because its volume is unreliable.

```
$ agentcad measure lid/build/stl/box.stl
PASS   measure  box.stl  60.00 x 40.00 x 30.00 mm, 15552.0 mm3, 15952.0 mm2
1 pass, 0 warn, 0 fail
```

## agentcad interfere

```
agentcad interfere FILE... [--tol MM3] [--json]
```

Overlap volume of every pair of files, in the positions the files are in. FAIL above `--tol` (default 0.01). At
least two files. Use `build/assembly/*.stl` to compare parts in the assembled pose.

```
$ agentcad interfere bad/build/assembly/left.stl bad/build/assembly/right.stl
FAIL   interference  left.stl+right.stl  overlap 4000.000 mm3
0 pass, 0 warn, 1 fail
```

## agentcad overhang

```
agentcad overhang FILE... [--max-deg 45] [--allow MM2] [--first-layer 0.3] [--json]
```

Unsupported downward faces, with the file taken as the print frame (bed at z = 0, such as `build/stl/*.stl`). A face
is unsupported when it faces down and its tilt from vertical exceeds `--max-deg` (a face exactly at the limit
passes); faces lying on the bed are excluded, and so are downward faces centred below `--first-layer` mm (default 0.3,
0 disables), such as a bottom-edge chamfer that prints as part of the first layers. Their area is reported as
`first_layer_area_mm2`. FAIL when the total area exceeds `--allow` (default 25). The JSON `where` lists the five largest regions
with centroid, area and z range.

```
$ agentcad overhang bad/build/stl/tee.stl
FAIL   overhang  tee.stl  300.0 mm2 unsupported at 45 deg (allowed 25)
0 pass, 0 warn, 1 fail
```

## agentcad wall

```
agentcad wall FILE... [--samples N] [--thin MM] [--bands MM] [--up] [--zmin Z] [--zmax Z] [--rmin R] [--rmax R]
              [--json]
```

A port of the `stlwall` tool. It samples the surface evenly (seeded, so runs repeat), casts a ray inward along each
face normal and records the distance to the far side. Hits closer than 0.05 mm are grazing rays and are dropped. Rays
start 0.01 mm inside the surface, so a 1.0 mm wall reads 0.99.

| Option | Default | Effect |
|---|---|---|
| `--samples` | 60000 | surface samples |
| `--thin` | 1.9 | report the share of surface thinner than this (mm) |
| `--bands` | off | also tabulate min / p2 / median per z band of this height (mm) |
| `--up` | off | only faces pointing up (normal z > 0.3) |
| `--zmin`, `--zmax` | none | only samples inside this z range |
| `--rmin`, `--rmax` | none | only samples this far from the part's XY bounding-box centre |

It is a measurement, so the status is PASS; it WARNs only when nothing was hit (open mesh or empty selection). To
fail a build on thin walls set `checks.min_wall_mm` and run `agentcad check`.

```
$ agentcad wall bad/build/stl/thin_cup.stl --samples 20000 --bands 10
PASS   wall  thin_cup.stl  20000/20000 hits, min 0.59 p1 0.59 p5 0.59 median 0.59 mm, under 1.9: 95.58% of surface (z 0..20)
  thin_cup.stl z    0.0-  10.0: min 0.59 p2 0.59 median 0.59  (n=12496)
  thin_cup.stl z   10.0-  20.0: min 0.59 p2 0.59 median 0.59  (n=7306)
1 pass, 0 warn, 0 fail
```

## agentcad fit

```
agentcad fit FILE... (--printer NAME | --bed XxYxZ) [--json]
```

Does each file's bounding box fit the bed, with 2 mm margin on each side in XY? A quarter turn about Z is tried
and reported. The height must fit as well. `--printer` takes a built-in or user profile (see `printers`).

```
$ agentcad fit bad/build/stl/long_bar.stl --printer generic-220
FAIL   bed_fit  long_bar.stl  300.0 x 10.0 x 5.0 mm does not fit generic-220 220x220x250
0 pass, 0 warn, 1 fail
```

## agentcad render

```
agentcad render [PROJECT | FILE...] [-o DIR] [--frame use|print] [--views iso,front,right,top] [--section AXIS=MM]
                [--json]
```

Offscreen PNG views plus a combined `sheet.png`. For a project it builds first, then renders
`build/assembly/*.stl` (`--frame use`, the default) or `build/stl/*.stl` (`--frame print`; the parts are laid out in
a row because they all sit at the origin). Part colours come from `Part.color`. The default output folder is
`PROJECT/build/renders`, or `./renders` for files. `--section z=10` cuts with a plane and fills the cut face. Exit 2
if the render module or offscreen rendering is unavailable; `agentcad doctor` says why.

```
$ agentcad render lid -o out_r
out_r/iso.png
out_r/front.png
out_r/right.png
out_r/top.png
out_r/sheet.png
```

## agentcad view

```
agentcad view [PROJECT | PATH...] [--port N] [--no-open] [--bed WxD] [--color PATTERN=#RRGGBB[@OPACITY]] [-r]
```

Starts the local three.js viewer on the loopback interface and blocks until interrupted. For a project it builds
first, shows the assembly, draws the printer bed and uses the part colours. `--color` matches a pattern against file
names (repeatable); `-r` walks folders recursively. It has no JSON output.

```
$ agentcad view lid --no-open --port 8765
agentcad view: http://127.0.0.1:8765/
```

## agentcad diff

```
agentcad diff OLD NEW [--json]
```

Compares two builds: parts added or removed, and per part the change in volume, in the bounding-box size of each
frame, and in `qty`, `printed` and `solids`. OLD and NEW are `manifest.json` files, build folders, or project
folders that have a `build/`. It reports differences and exits 0; read `changed` in the JSON.

```
$ agentcad diff lid lid2
~ lid: volume 6888.0 -> 6873.6 mm3 (-14.4)
```

## agentcad printers

```
agentcad printers [--json]
```

Lists the built-in generic profiles and any in `~/.config/agentcad/printers.toml`:

```
[printers.my-printer]
bed = [250, 250, 250]
nozzle_mm = 0.4
```

```
$ agentcad printers
generic-220    220x220x250  nozzle 0.4 mm
generic-256    256x256x256  nozzle 0.4 mm
generic-180    180x180x180  nozzle 0.4 mm
generic-300    300x300x300  nozzle 0.4 mm
generic-350    350x350x340  nozzle 0.4 mm
```

## agentcad drawing

```
agentcad drawing [PROJECT|FILE ...] [-o DIR] [--parts NAME,NAME] [--format svg,pdf,png]
                 [--projection third|first] [--sheet auto|A4|A3|letter|tabloid] [--json]
```

Writes one dimensioned sheet per part: front, top, right and isometric views, overall sizes, hole callouts and
positions, and a title block. Every dimension is read from the built solid. The input is a project folder (default
`.`) or STEP files; meshes are refused with exit 2 because they have no edges. Output goes to
`PROJECT/build/drawings`, `./drawings` for STEP files, or `-o DIR`, as `<part>.svg`, `.pdf` and `.png`. An unknown
`--parts` name exits 2 and lists the valid names; `--json` prints `agentcad.drawing/1` with the scale, sheet, views
and every dimension (`id`, `view`, `kind`, `role`, `value_mm`, `text`) so numbers can be checked without reading the
image. Layout, scale rules and the JSON shape are in [drawings.md](drawings.md).

```
$ agentcad drawing flat_panel --format svg,png
/path/to/flat_panel/build/drawings/panel.svg
/path/to/flat_panel/build/drawings/panel.png
/path/to/flat_panel/build/drawings/bracket.svg
/path/to/flat_panel/build/drawings/bracket.png
```

## agentcad api

```
agentcad api [--json]
```

Prints everything an agent would otherwise guess, from the code itself: every command with its flags, the
`CheckContext` methods and signatures, the `agentcad.toml` keys with defaults (including `[flat]`), every gate id,
the printer profiles, the templates, the three frames and the report fields. The text form is a cheat sheet of about
6 KB; `--json` prints the same as `agentcad.api/1` (about 12 KB) with the keys `commands`, `exit_codes`, `model`,
`context`, `config`, `gates`, `printers`, `templates`, `frames` and `report`.

```
$ agentcad api
# agentcad API cheat sheet
*agentcad 0.2.0, schema agentcad.api/1*
...
```

## agentcad skill

```
agentcad skill {install|show|path} [--host claude|codex|cursor|agents-md] [--project DIR] [--link] [--force]
               [--reference NAME] [--json]
```

Puts the agent skill where an agent will read it, or prints it. The skill ships inside the package, so this works
from a plain `uv tool install`.

**install** copies the skill for `--host` (default `claude`) and prints where it went; `--json` prints
`agentcad.skill/1` with `action`, `host`, `dest`, `status` (`installed`, `updated` or `unchanged`) and `files`.
Running it again is a no-op when the copy is current.

| Host | What it writes | Where |
|---|---|---|
| `claude` | `SKILL.md` and `references/` | `~/.claude/skills/agentcad`; with `--project DIR`, `DIR/.claude/skills/agentcad` |
| `codex` | `SKILL.md` and `references/` | `$CODEX_HOME/skills/agentcad`, default `~/.codex/skills/agentcad`. User-wide: `--project` and `--link` are refused (exit 2) |
| `cursor` | `agentcad.mdc` rule and the references | `.cursor/rules/` in `--project`, default the current folder. `--link` is refused |
| `agents-md` | a condensed guide between `<!-- agentcad:begin -->` and `<!-- agentcad:end -->` | `AGENTS.md` in `--project`, default the current folder; text outside the markers is left alone. `--link` is refused |

- `--link` (claude only) makes the destination a symlink to the installed skill, so it follows upgrades.
- A destination that exists and differs is refused with exit 2 unless `--force`. `--force` only replaces a folder
  that has a `SKILL.md`, so it cannot delete an unrelated directory.
- Symlink guard: when `--project` is given, an install refuses (exit 2) to write through a symlink inside that
  project, such as a `.claude` link planted by a hostile repository.

**show** prints `SKILL.md`, or one reference file with `--reference NAME` (`cadquery-patterns`, `cut-parts`,
`design-for-printing`, `verification-playbook`); an unknown name exits 2 and lists the choices. `--json` wraps the
text in `agentcad.skill/1` with `path` and `content`.

**path** prints the folder that holds the skill, for linking by hand.

```
$ agentcad skill install --project demo
installed agentcad skill for claude: /path/to/demo/.claude/skills/agentcad
$ agentcad skill install --project demo
already up to date: /path/to/demo/.claude/skills/agentcad
```

## agentcad mcp

```
agentcad mcp [--root DIR]
```

Runs an MCP server on stdio so an agent can call agentcad as tools without shelling out. It needs the `mcp` extra
(`uv tool install 'agentcad[mcp] @ git+https://github.com/AryaVora621/agentcad'`). `--root DIR` refuses paths outside
that folder. Register it with `claude mcp add agentcad -- agentcad mcp`. The tools mirror the commands above; the
list, arguments and behaviour are in [mcp.md](mcp.md).

## agentcad doctor

```
agentcad doctor [--json]
```

Versions, whether VTK offscreen rendering works, write access to the current folder and the user printer file. Exit 1
if write access or the printer file fails; a missing render stack is a WARN.

```
$ agentcad doctor
PASS   python        -     Python 3.12.13 on Darwin
PASS   agentcad      -     agentcad 0.2.0
PASS   cadquery      -     CadQuery 2.8.0, trimesh 5.2.0
PASS   offscreen     -     VTK 9.6.2 offscreen OK
PASS   write_access  -     can write to /path/to/folder
PASS   printers      -     5 built-in, 0 user profile(s)
6 pass, 0 warn, 0 fail
```

## Writing project checks

`checks(ctx)` receives a `CheckContext`. Every argument that names a part also takes a `cq.Shape` or
`cq.Workplane` in the use frame (a probe solid, such as a screw stand-in built from the requirement), and
`against` also takes a list.

| Call | Returns |
|---|---|
| `ctx.part(name)`, `ctx.solid(name, frame="use")` | the `Part`, or its built solid |
| `ctx.interference(a, b)` | overlap in mm3 |
| `ctx.distance(a, b)` | minimum distance in mm, 0 if touching |
| `ctx.section(names, axis, at)` | `{"area_mm2", "bbox_mm", "loops"}`; areas are summed per shape |
| `ctx.slide(moving, against, direction, distance, steps=40)` | `[{"offset_mm", "overlap_mm3"}, ...]`; `direction` is an `(x, y, z)` tuple, normalised |
| `ctx.play(moving, against, direction, max_mm=5.0, resolution_mm=0.01)` | free travel in mm, `None` if it never touches |
| `ctx.expect(id, value, *, min=None, max=None, part=None, unit="mm", summary=None)` | records PASS or FAIL; needs `min` or `max` |
| `ctx.record(check)` | adds a hand-built `Check` |

`unit` names the keys: `unit="mm3"` stores `value_mm3`, `min_mm3` and `max_mm3`.
