# agentcad architecture

This document is the contract every module is built against. If code and this document disagree, fix one of them in
the same change.

## 1. Why agentcad exists

Language models can write CAD code, but they cannot see what it builds. Left alone, an agent "verifies" a part by
reading its own parameters back, passes tests that compare a value with itself, and reports success while parts
overlap, print upside down, or have walls too thin to slice. Renders do not help much: thin walls, hidden overlaps and
missing clearances are invisible in a screenshot.

agentcad closes the loop. A part is defined in Python (CadQuery), built, exported and then **measured from the built
solids**. Every check returns numbers, a limit, a status and, where it applies, a location the agent can act on. The
same data comes out as JSON for agents and as a short table for people. Renders and a live viewer cover what numbers
cannot.

Design rules for every command and API:

1. **Measure, never echo.** A check reads the built solid or the exported mesh, never the parameters that produced it.
2. **Machine-readable first.** Every command takes `--json` and emits one JSON document on stdout with a versioned
   schema. Human output is a compact table. Logs go to stderr.
3. **Actionable failures.** A FAIL says what was measured, what the limit was, and where (part, z range, xyz centroid).
4. **Deterministic and headless.** No GUI is needed for anything except `agentcad view`. Random sampling is seeded.
5. **Exit codes are the API.** `0` all checks pass (WARN allowed), `1` at least one FAIL, `2` usage or config error,
   `3` the model code raised or produced no valid solid.
6. **Every gate can fail.** Each built-in check ships with a fixture in the test suite that must FAIL it.

## 2. Repository layout

```
pyproject.toml            hatchling build, console script `agentcad = agentcad.cli:main`
src/agentcad/
  __init__.py             public API re-exports, __version__
  model.py                Pose, Part
  project.py              agentcad.toml loading, build module import, Project, BuildResult
  report.py               Status, Check, Report, JSON and table rendering, exit codes
  geometry.py             shape helpers: to_shape, place, bbox, volume, area, center of mass, mesh export/load
  measure.py              interference, distance, section, slide (motion) tests
  printability.py         overhang scan, bed fit, bed contact area
  wall.py                 ray-cast wall thickness (port of the stlwall tool)
  printers.py             built-in printer profiles + user profiles
  checks.py               CheckContext and the built-in gates run by `agentcad check`
  build.py                build pipeline: build parts, export files, write manifest.json
  diff.py                 manifest diff
  render.py               offscreen VTK renders (multi-view sheet, section views)
  viewer/                 local three.js viewer (port of the cadview tool): server.py + static/index.html
  scaffold/               `agentcad new` templates
  cli.py                  argparse entry point for every subcommand
skills/agentcad/          agent skill (SKILL.md + references/) for Claude Code and other agents
examples/                 bracket, box_with_lid, broken (a project where every gate must FAIL)
tests/                    pytest suite with known-answer fixtures
docs/                     this file, CLI reference, report schema
```

## 3. Data model (`model.py`)

```python
@dataclass(frozen=True)
class Pose:
    x: float = 0; y: float = 0; z: float = 0      # mm
    rx: float = 0; ry: float = 0; rz: float = 0   # degrees
    def location(self) -> cq.Location             # rotate about X, then Y, then Z (extrinsic, about the
                                                  # part's own origin), then translate
    def apply(self, shape: cq.Shape) -> cq.Shape  # returns a moved copy

@dataclass
class Part:
    name: str                                     # [a-z0-9_-]+, unique within a project
    shape: cq.Workplane | cq.Shape                # the model, in its own frame
    use: Pose = Pose()                            # placement in the assembled product
    print: Pose | Literal["as-is"] = "as-is"      # rotation for printing; translation is ignored
    qty: int = 1
    material: str | None = None                   # free text, e.g. "PETG"
    color: str | None = None                      # "#rrggbb", used by render and view
    printed: bool = True                          # False for bought parts: skip print gates
    def solid(self, frame: Literal["model", "use", "print"]) -> cq.Shape
```

`solid("print")` applies the print rotation, then translates so the bounding box minimum z is 0 and the XY bounding box
is centred on the origin. That is the frame the printability gates and `build/stl/` use.

## 4. Projects (`project.py`)

A project is a folder with an `agentcad.toml`:

```toml
[project]
name = "box-with-lid"
module = "model.py"            # path relative to this file; must define parts() -> list[Part]
                               # and may define checks(ctx) -> None

[printer]
profile = "generic-220"        # a built-in or user profile name, or:
# bed = [350, 350, 345]        # X Y Z in mm (overrides profile)

[export]
tolerance = 0.02               # mm, STL linear deflection
angular_tolerance = 0.05       # rad

[checks]
max_overhang_deg = 45          # tilt of a downward face from vertical (0 wall, 90 flat ceiling)
overhang_allow_mm2 = 25        # unsupported area allowed per part before FAIL
min_wall_mm = 0                # 0 disables the wall gate; otherwise FAIL below this
interference_tol_mm3 = 0.01    # overlap below this counts as touching
allow_overlap = [["lid", "box"]]   # pairs that may overlap in the use pose (press fits, snap barbs)
```

Every key is optional except `project.module`. Unknown keys are a config error (exit 2) so typos do not silently
disable a gate.

`Project.load(path)` finds `agentcad.toml` (a path to the file or its folder), imports the module in a fresh namespace
with the project folder on `sys.path`, calls `parts()` and validates names. `Project.parts` is a dict name -> Part.

## 5. Reports (`report.py`)

```python
class Status(str, Enum): PASS, WARN, FAIL, SKIP, ERROR

@dataclass
class Check:
    id: str                    # "overhang", "interference", or a project check name
    part: str | None           # part name, "a+b" for pairs, None for project-wide
    status: Status
    summary: str               # one line a person or agent can act on
    measured: dict             # numbers with unit-suffixed keys: {"area_mm2": 41.2, "z_mm": [0.0, 12.4]}
    limit: dict                # the thresholds used: {"max_area_mm2": 25}
    where: list[dict] = []     # up to 5 worst locations: {"centroid_mm": [x,y,z], "area_mm2": .., "z_mm": [..]}

@dataclass
class Report:
    tool: str                  # "check", "measure", ...
    project: str | None
    checks: list[Check]
    meta: dict                 # agentcad version, cadquery version, elapsed_s, printer, frame used
    def status(self) -> Status # FAIL if any FAIL/ERROR, else WARN if any WARN, else PASS
    def exit_code(self) -> int # 0 / 1, ERROR maps to 3
    def to_json(self) -> str   # {"schema": "agentcad.report/1", ...}, floats rounded to 4 decimal places
    def to_table(self) -> str  # one line per check: STATUS  id  part  summary; last line "N pass, N warn, N fail"
```

The JSON schema is documented in `docs/report-schema.md` and is stable within a major version.

## 6. Geometry and measurement (`geometry.py`, `measure.py`)

```python
to_shape(obj) -> cq.Shape                       # Workplane -> fused Shape (Compound if several solids)
bbox(shape) -> dict                             # {"min_mm": [..], "max_mm": [..], "size_mm": [..]}
volume(shape) -> float; area(shape) -> float; center_of_mass(shape) -> [x, y, z]
solids_count(shape) -> int; is_valid(shape) -> bool
to_mesh(shape, tolerance, angular_tolerance) -> trimesh.Trimesh   # via a temp STL export
load(path) -> cq.Shape | trimesh.Trimesh        # .step/.stp -> Shape; .stl/.obj/.ply/.3mf/.glb -> Trimesh

interference(a, b) -> float                     # mm3 of a.intersect(b); BRep for Shapes, manifold3d for meshes
distance(a, b) -> float                         # min distance mm (BRepExtrema_DistShapeShape); 0 if touching or
                                                # overlapping
section(shapes, axis="z", at=0.0) -> dict       # {"area_mm2", "bbox_mm", "loops"} of the planar cut
slide(moving, against, direction, distance, steps) -> list[dict]
    # translate `moving` along `direction` (normalised) from 0 to `distance` in `steps` steps and return
    # [{"offset_mm": d, "overlap_mm3": v}, ...] against the union of `against`. Used to prove a lid lifts free,
    # a drawer slides out, or a latch only overlaps at its barb.
```

## 7. Printability (`printability.py`, `wall.py`, `printers.py`)

- `overhang(mesh, max_deg=45, bed_tol=0.05) -> dict`: in the print frame, a face is unsupported when it faces down
  and its tilt from vertical exceeds `max_deg` (normal z < -sin(max_deg)), unless every vertex sits within `bed_tol`
  of z = 0. Unsupported faces are grouped into connected regions (shared edges); the result holds the total area,
  the region count and the five largest regions with area, z range and centroid.
- `bed_fit(mesh_or_bbox, bed_xyz, margin=2.0) -> dict`: fits if the print-frame bbox fits inside the bed with `margin`
  on each side in XY (rotating 90 degrees about Z is tried and reported).
- `bed_contact(mesh, bed_tol=0.05) -> float`: area of faces on z = 0, mm2. A WARN when under 1 % of the XY footprint
  area or under 20 mm2 (likely to tip or detach).
- `wall_thickness(mesh, samples=60000, seed=0, thin=None, zmin=None, zmax=None) -> dict`: samples the surface evenly,
  casts a ray inward along the negative face normal, records the distance to the next hit; returns min, p1, p5,
  median, the share under `thin` and the z range of thin samples. Hits under 0.05 mm are grazing rays at edges and are
  dropped.
- `printers.py`: `PROFILES = {name: Printer(name, bed_xyz, nozzle_mm, notes)}` with generic sizes
  (`generic-220` 220x220x250, `generic-256` 256x256x256, `generic-180` 180x180x180, `generic-300` 300x300x300,
  `generic-350` 350x350x340) and `load_user_profiles()` from `~/.config/agentcad/printers.toml`. Brand-named profiles
  are only added with a cited spec.

## 8. Checks (`checks.py`)

`agentcad check` builds the project (unless `--no-build` and a fresh `build/` exists), then runs these gates:

| id | per | FAIL when |
|---|---|---|
| `valid` | part | the shape is not valid, has no solid, or volume <= 0 (ERROR when `parts()` raises) |
| `single_solid` | part | WARN when a part is more than one disconnected solid |
| `watertight` | part | the exported print STL is not watertight or not winding-consistent |
| `bed_fit` | printed part | the print-frame bbox does not fit the printer (either XY orientation) |
| `bed_contact` | printed part | WARN only, see section 7 |
| `overhang` | printed part | unsupported area > `overhang_allow_mm2` |
| `min_wall` | printed part | enabled by `min_wall_mm > 0`; FAIL when p1 < `min_wall_mm` |
| `interference` | each part pair | use-pose overlap > `interference_tol_mm3`, unless the pair is in `allow_overlap` |

Then it calls the project's `checks(ctx)` if defined. `CheckContext` gives project checks:

```python
ctx.part(name) -> Part
ctx.solid(name, frame="use") -> cq.Shape
# Every argument below that names a part also accepts a cq.Shape / cq.Workplane already in the use frame. That is
# how probe solids work: build a stand-in for the mating thing (an M4 screw, a 54 mm ball, a cable) from the
# requirement, not from the model's parameters, and measure against it.
ctx.interference(a, b) -> float              # use frame, mm3
ctx.distance(a, b) -> float                  # use frame, mm
ctx.section(names, axis, at) -> dict
ctx.slide(moving, against, direction, distance, steps=40) -> list[dict]
ctx.play(moving, against, direction, max_mm=5.0, resolution_mm=0.01) -> float | None
    # free travel of `moving` along `direction` before it overlaps `against` (bisection on interference > tol);
    # None when it never touches within max_mm. Measures fit clearances (a lid's side play, a pin in a hole).
ctx.expect(id, value, *, min=None, max=None, part=None, unit="mm", summary=None) -> Check
    # records PASS when min <= value <= max, else FAIL with measured/limit filled in
ctx.record(check: Check) -> Check
```

A project check that raises is recorded as ERROR with the traceback's last line, and the run continues.

## 9. Build outputs (`build.py`)

`agentcad build` writes to `<project>/build/` (or `--out`):

```
manifest.json         schema agentcad.manifest/1: project, agentcad version, created, export tolerances, printer,
                      and per part: name, qty, printed, material, volume_mm3, area_mm2, bbox (model/use/print),
                      center_of_mass_mm (use), solids, valid, triangles, sha256 of each file
step/<part>.step      model frame
stl/<part>.stl        print frame (on z = 0, XY centred)
assembly/<part>.stl   use frame
assembly.step         every part in the use frame, one named solid per part
```

The build is atomic: it writes into `build/.tmp-<pid>` and renames on success, so a crashed build never leaves stale
files that look fresh. `report.json` is written next to `manifest.json` by `agentcad check`.

## 10. CLI (`cli.py`)

```
agentcad new DIR [--template blank|bracket|box-with-lid]
agentcad build [PROJECT] [--out DIR] [--json]
agentcad check [PROJECT] [--json] [--only ID,ID] [--no-build]
agentcad measure FILE... [--json]                 bbox, volume, area, CoM, solids/watertight for STEP or meshes
agentcad interfere FILE... [--json] [--tol MM3]   pairwise overlap between files as placed
agentcad overhang FILE... [--max-deg 45] [--allow MM2] [--json]
agentcad wall FILE... [--thin MM] [--samples N] [--bands MM] [--up] [--zmin Z] [--zmax Z] [--rmin R] [--rmax R]
              [--json]                            all options of the original stlwall tool; --bands adds a per-z-band
                                                  min / p2 / median table, --up samples only upward faces (nz > 0.3),
                                                  --rmin/--rmax filter by radius from the part's XY bbox centre
agentcad fit FILE... (--printer NAME | --bed XxYxZ) [--json]
agentcad render [PROJECT | FILE...] [-o DIR] [--frame use|print] [--views iso,front,right,top] [--section AXIS=MM]
agentcad view [PROJECT | PATH...] [--port N] [--no-open] [--bed WxD] [--color PATTERN=#RRGGBB[@OPACITY]] [-r]
agentcad diff OLD NEW [--json]                    compare two manifest.json files (volume, bbox, part set)
agentcad printers [--json]
agentcad doctor [--json]                          versions, VTK offscreen check, write access
```

`PROJECT` defaults to the current folder. Commands that accept files work without a project. Exit codes as in
section 1.

## 11. Render and viewer

- `render.render_sheet(meshes: list[(name, trimesh, color)], out_png, views=("iso","front","right","top"),
  size=(1600, 1200), section=None) -> list[str]` writes one PNG per view plus `sheet.png` (2x2 grid), each labelled
  with the view name, overall size in mm and a scale bar. Offscreen VTK, feature-angle normals, parallel projection
  for orthographic views. `section=("z", 10.0)` cuts with `vtkClipClosedSurface` so cut faces are filled.
- `viewer.serve(paths, port=None, open_browser=True, bed=None, colors=(), recursive=False)`: the local three.js
  viewer, loopback only, live reload. `~/.local/bin/cadview`-style invocations map onto `agentcad view`.

## 12. Agent skill

`skills/agentcad/SKILL.md` teaches an agent the loop: scaffold, model, `agentcad check --json`, read FAILs, fix, render,
look, repeat; how to turn every functional requirement into a `ctx.expect` or `ctx.slide` check; how to prove a check
can fail; and the design-for-printing and CadQuery references in `skills/agentcad/references/`.

## 13. Roadmap after v0.1

- MCP server (`agentcad mcp`) exposing build, check, measure and render as tools, returning render PNGs inline.
- Sliding explode view in the viewer (per-part explode vectors from the manifest).
- Slicer gate (OrcaSlicer / PrusaSlicer CLI) for support volume and print time.
- build123d adapter for `Part.shape`.
