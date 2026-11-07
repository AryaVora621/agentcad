# CLI reference

Every command takes `--json` (except `view`) and then prints exactly one JSON document on stdout. Without it the
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

## agentcad check

```
agentcad check [PROJECT] [--json] [--only ID,ID] [--no-build]
```

Builds the project, runs the built-in gates, then the project's own checks, and writes the report to
`build/report.json` as well as printing it.

- `--only` runs just the listed check ids. Built-in ids: `valid`, `single_solid`, `watertight`, `bed_fit`,
  `bed_contact`, `overhang`, `min_wall`, `interference`. Project check ids are the ones passed to `ctx.expect`.
  An id that matches nothing is exit 2.
- `--no-build` reuses `build/` only if the sources and every exported file still match `manifest.json`; otherwise
  it exits 2 and tells you to build.

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

## agentcad doctor

```
agentcad doctor [--json]
```

Versions, whether VTK offscreen rendering works, write access to the current folder and the user printer file. Exit 1
if write access or the printer file fails; a missing render stack is a WARN.

```
$ agentcad doctor
PASS   python        -     Python 3.12.13 on Darwin
PASS   agentcad      -     agentcad 0.1.0
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
