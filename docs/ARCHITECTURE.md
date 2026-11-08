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
  flat.py                 2D cut parts: section profile, DXF and SVG export, the five flat gates
  build.py                build pipeline: build parts, export files, write manifest.json
  diff.py                 manifest diff
  render.py               offscreen VTK renders (multi-view sheet, section views)
  drawing.py              dimensioned SVG, PDF and PNG drawings from the built solids
  api.py                  `agentcad api`: commands, ctx helpers, config keys and gates read from the code
  skill_install.py        `agentcad skill`: install, show or locate the agent skill
  mcp_server.py           `agentcad mcp`: the CLI as MCP tools on stdio (needs the `mcp` extra)
  viewer/                 local three.js viewer (port of the cadview tool): server.py + static/index.html
  scaffold/               `agentcad new` templates
  cli.py                  argparse entry point for every subcommand
skills/agentcad/          agent skill (SKILL.md + references/) for Claude Code and other agents; packed into the
                          wheel and found by `agentcad skill`
examples/                 bracket, box_with_lid, flat_panel, broken (a project where every gate must FAIL)
tests/                    pytest suite with known-answer fixtures
docs/                     this file, CLI reference, report schema, flat parts, drawings, MCP server
llms.txt                  index of the docs and the skill for agents that read a project index
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
    cut: Literal["laser", "cnc", "waterjet"] | None = None   # a sheet part: print frame is the cutting frame, the
                                                  # 3D printing gates are skipped and the flat gates run (section 12)
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
first_layer_mm = 0.3           # downward faces centred below this print with the first layers (0 disables)
min_wall_mm = 0                # 0 disables the wall gate; otherwise FAIL below this
interference_tol_mm3 = 0.01    # overlap below this counts as touching
allow_overlap = [["lid", "box"]]   # pairs that may overlap in the use pose (press fits, snap barbs)

[flat]                         # only used by parts with cut=...; defaults and meaning in section 12
sheet = [600, 400]
kerf_mm = 0.15
min_feature_mm = 1.0
tool_diameter_mm = 3.175
min_hole_ratio = 1.0
compensate_kerf = false
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
    hint: str | None = None    # how to fix a FAIL or WARN, naming the numbers and the Part or config field

@dataclass
class Report:
    tool: str                  # "check", "measure", ...
    project: str | None
    checks: list[Check]
    meta: dict                 # agentcad version, cadquery version, elapsed_s, printer, frame used
    def status(self) -> Status # FAIL if any FAIL/ERROR, else WARN if any WARN, else PASS
    def exit_code(self) -> int # 0 / 1, ERROR maps to 3
    def to_json(self, view="full") -> str   # {"schema": "agentcad.report/1", ...}, floats rounded to 4 decimal places;
                                            # view="brief" keeps only non-PASS checks and groups the rest in "passed"
    def to_table(self) -> str  # one line per check: STATUS  id  part  summary; last line "N pass, N warn, N fail"
    def to_brief_table(self) -> str   # non-PASS rows with an indented "hint:" line, then "N passed: id xN, ..." and totals
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

interference(a, b) -> float                     # mm3 of overlap; see "Robust overlap" below
distance(a, b) -> float                         # min distance mm (BRepExtrema_DistShapeShape); 0 if touching or
                                                # overlapping
section(shapes, axis="z", at=0.0) -> dict       # {"area_mm2", "bbox_mm", "loops"} of the planar cut
slide(moving, against, direction, distance, steps) -> list[dict]
    # translate `moving` along `direction` (normalised) from 0 to `distance` in `steps` steps and return
    # [{"offset_mm": d, "overlap_mm3": v}, ...] against the union of `against`. Used to prove a lid lifts free,
    # a drawer slides out, or a latch only overlaps at its barb.
```

Robust overlap. OCCT's boolean common is order dependent when faces are coplanar: for a lid resting on a box rim
with an oversized lip, `box.intersect(lid)` returned a shell with no solid (and a bogus volume) while
`lid.intersect(box)` returned the true 558 mm3. Counting only solids turned a real overlap into a silent PASS.
`measure.overlap` therefore computes the BRep common in both orders and accepts it only when both results are clean
(no shell or face residue outside solids) and agree within max(1e-6, 1e-4 x larger). Otherwise it intersects the
tessellated meshes with manifold3d and returns the largest of the three volumes, so disagreement always errs toward
FAIL. `section` sums cut areas per shape (fusing touching solids fails in OCCT), so overlapping solids count twice.
`bbox` uses exact analytic bounds, because tessellation-grown boxes put print-frame minimum z slightly below 0.

## 7. Printability (`printability.py`, `wall.py`, `printers.py`)

- `overhang(mesh, max_deg=45, bed_tol=0.05) -> dict`: in the print frame, a face is unsupported when it faces down
  and its tilt from vertical exceeds `max_deg` (normal z < -sin(max_deg)), unless every vertex sits within `bed_tol`
  of z = 0. A face exactly at the limit passes (FAIL only above `max_deg` + 0.01 degrees). Downward faces whose
  centroid z is below `first_layer_mm` (default 0.3) print with the first layers, for example an anti elephant-foot
  chamfer on a bottom edge, and are not counted; their area is reported as `first_layer_area_mm2` so nothing is
  hidden. Unsupported faces are grouped into connected regions (shared edges); the result holds the total area,
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
| `flat_profile`, `sheet_fit`, `min_feature`, `inner_corners`, `hole_size` | cut part | see section 12 |

Parts with `cut` set are left out of the five 3D printing gates (`watertight`, `bed_fit`, `bed_contact`, `overhang`,
`min_wall`); `valid`, `single_solid` and `interference` still run. `checks._builtin` appends `flat.flat_gates(ctx,
wanted)` after the other gates. Every built-in FAIL or WARN carries a `hint`.

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

A project check that raises is recorded as ERROR with the traceback's last line, and the run continues. Besides
`checks(ctx)`, every module-level function named `check_*(ctx)` runs on its own, so one raising check does not stop
the others.

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
dxf/<part>.dxf        cut parts only: the profile in mm, layer CUT
svg/<part>.svg        cut parts only: the same loops, sized in mm
```

The build is atomic: it writes into `build/.tmp-<pid>` and swaps each output entry in only on success, so a crashed
build never leaves stale files that look fresh, while `build/renders/` and `report.json` survive a rebuild. The
manifest entry of a cut part also holds `cut` and a `flat` record (thickness, profile area, cut length, hole count,
outline, kerf settings, file hashes). The manifest also records `files` and `sources` (sha256), which `check --no-build` uses to refuse a stale build.
`report.json` is written next to `manifest.json` by `agentcad check`; reports also carry `status`, `exit_code` and
`counts`.

## 10. CLI (`cli.py`)

```
agentcad new DIR [--template blank|bracket|box-with-lid]
agentcad build [PROJECT] [--out DIR] [--json]
agentcad check [PROJECT] [--json] [--only ID,ID] [--no-build] [--brief]
agentcad measure FILE... [--json]                 bbox, volume, area, CoM, solids/watertight for STEP or meshes
agentcad interfere FILE... [--json] [--tol MM3]   pairwise overlap between files as placed
agentcad overhang FILE... [--max-deg 45] [--allow MM2] [--first-layer MM] [--json]
agentcad wall FILE... [--thin MM] [--samples N] [--bands MM] [--up] [--zmin Z] [--zmax Z] [--rmin R] [--rmax R]
              [--json]                            all options of the original stlwall tool; --bands adds a per-z-band
                                                  min / p2 / median table, --up samples only upward faces (nz > 0.3),
                                                  --rmin/--rmax filter by radius from the part's XY bbox centre
agentcad fit FILE... (--printer NAME | --bed XxYxZ) [--json]
agentcad render [PROJECT | FILE...] [-o DIR] [--frame use|print] [--views iso,front,right,top] [--section AXIS=MM]
agentcad view [PROJECT | PATH...] [--port N] [--no-open] [--bed WxD] [--color PATTERN=#RRGGBB[@OPACITY]] [-r]
agentcad diff OLD NEW [--json]                    compare two manifest.json files or build folders (volume, bbox,
                                                  part set); schema agentcad.diff/1, exit 0 whenever it can compare
agentcad printers [--json]
agentcad doctor [--json]                          versions, VTK offscreen check, write access
agentcad drawing [PROJECT|FILE ...] [-o DIR] [--parts NAME,NAME] [--format svg,pdf,png]
                 [--projection third|first] [--sheet auto|A4|A3|letter|tabloid] [--json]
agentcad api [--json]                             commands, ctx helpers, config keys, gates
agentcad skill {install|show|path} [--host claude|codex|cursor|agents-md] [--project DIR] [--link] [--force]
               [--reference NAME] [--json]
agentcad mcp [--root DIR]                         MCP server on stdio; no --json
```

`PROJECT` defaults to the current folder. Commands that accept files work without a project. Exit codes as in
section 1. `cli.py` imports `drawing`, `api`, `skill_install` and `mcp_server` lazily inside their handlers, so a
command that does not need them never pays for their imports.

## 11. Render and viewer

- `render.render_sheet(meshes: list[(name, trimesh, color)], out_png, views=("iso","front","right","top"),
  size=(1600, 1200), section=None) -> list[str]` writes one PNG per view plus `sheet.png` (2x2 grid), each labelled
  with the view name, overall size in mm and a scale bar. Offscreen VTK, feature-angle normals, parallel projection
  for orthographic views. `section=("z", 10.0)` cuts with `vtkClipClosedSurface` so cut faces are filled.
- `viewer.serve(paths, port=None, open_browser=True, bed=None, colors=(), recursive=False)`: the local three.js
  viewer, loopback only, live reload. Requests whose Host header is not `127.0.0.1:<port>`, `localhost:<port>` or
  `[::1]:<port>` get 403, which blocks DNS rebinding from other web pages. `python -m agentcad.viewer.server` keeps
  the command line of the standalone viewer it was ported from.

## 12. Flat parts (`flat.py`)

A cut part is modelled as a 3D plate and `Part(..., cut="laser"|"cnc"|"waterjet")` marks it. Its print frame is the
cutting frame: the sheet on `z = 0`, the thickness along `+z`. Everything is measured from a section of the built
solid, never from a parameter.

- `build_profile(solid, at=0.5, shift=None) -> Profile | None`: sections the print-frame solid at `at` times the
  thickness and returns the outer loop and the holes as `Loop`s (polylines with arc bulges, circles recognised),
  with the thickness, area and outline. Splines and ellipses are sampled to 0.005 mm. `None` when the plane misses
  the part.
- Gates, run by `flat_gates(ctx, wanted)` in `FLAT_IDS` order, one check per cut part, `where` in sheet coordinates
  (outline lower left is 0, 0): `flat_profile` (volume against profile area times thickness, and sections at 10 and
  90 percent against the middle one), `sheet_fit`, `min_feature` (morphological opening by half of
  `min_feature_mm` for webs, closing by half of `kerf_mm` for slots), `inner_corners` (CNC only, closing by the tool
  radius) and `hole_size`. The offsets run on manifold3d's `CrossSection` (Clipper2), which keeps tangent arcs and
  collapsing features stable. Area tolerances and the exact limits are in [flat.md](flat.md).
- `export_profile(solid, config, dxf_path, svg_path) -> dict` writes the DXF (R2010, layer `CUT`, circles and
  `LWPOLYLINE` with bulges) and the SVG, optionally with `compensated_loops` (outline grown and holes shrunk by
  `kerf_mm / 2`, re-anchored to 0, 0). The returned dict is the manifest's `flat` record. A hole or notch the
  compensation would collapse raises `ValueError`, which `build.py` reports as a `BuildError` (exit 3).
- `project.DEFAULT_FLAT` holds the `[flat]` defaults; unknown keys and bad values are `ConfigError`.

Limits: through-cuts only, one part per file (no nesting), settings are project-wide.

## 13. Drawings (`drawing.py`)

`make_drawings(inputs, out=None, parts=None, formats=("svg","pdf","png"), projection="third", sheet="auto") -> dict`
turns each part of a project, or each STEP file, into a dimensioned sheet and returns the `agentcad.drawing/1`
document. Meshes are refused (`ConfigError`) because they have no edges.

- `project_views` projects the exact solid with OCCT hidden-line removal into front, top, right and isometric views;
  visible sharp edges are solid, tangent edges thin, hidden edges dashed.
- Dimensions come from the exact bounding box and from circular edges seen end-on (`Hole`), never from parameters:
  overall width, height and depth, grouped diameter callouts, ordinate positions from the view's lower-left datum, or
  an id table beyond eight hole centres.
- `choose_sheet` picks the largest standard scale (10:1 to 1:50) at which views, dimension space and title block
  fit; A4 landscape, or A3 only when it allows a strictly larger scale. `plan_sheet` places every text, dimension
  and view so none overlaps another.
- Output is deterministic: there are no dates, and the same input gives byte-identical SVG, PDF and PNG. The title block carries the first 12 hex digits of a sha256 of the part's STEP data
  (`geometry_digest`).

Layout rules, what is dimensioned and the JSON shape are in [drawings.md](drawings.md). Not covered: tolerances,
section views, GD&T, threads, slots and angles.

## 14. API description (`api.py`)

`describe() -> dict` builds the `agentcad.api/1` document from the code itself, so it cannot drift: the commands and
flags come from `cli._parser()`, the `CheckContext` methods and signatures from `inspect`, the config keys and
defaults from `project`, the `Part` and `Pose` fields from the dataclasses, the gates from `GATE_DOCS` here plus
`flat.GATE_DOCS`, and the printers, templates, frames and report fields. `cheat_sheet(doc) -> str` renders it as
Markdown (about 6 KB; the JSON is about 12 KB). A new command, config key or gate shows up without editing the
document by hand, but a new gate still needs a line in `GATE_DOCS`.

## 15. Skill installer (`skill_install.py`)

`skill_dir()` finds the skill: the copy packed into the wheel (`agentcad/_skill`), else `skills/agentcad` in the
source tree. Hosts:

- `claude` and `codex` copy (or, for `claude`, symlink with `--link`) the folder to `.claude/skills/agentcad` or
  `$CODEX_HOME/skills/agentcad`.
- `cursor` writes `.cursor/rules/agentcad.mdc` (the skill body with frontmatter) and the references next to it.
- `agents-md` writes `AGENTS_BLOCK`, a condensed guide, between `<!-- agentcad:begin -->` and
  `<!-- agentcad:end -->` in `AGENTS.md`, leaving the rest of the file untouched.

Rules: an existing, different destination is refused unless `--force`; `--force` replaces a folder only when it holds
a `SKILL.md`; inside `--project` nothing is written through a symlink (`_no_symlinks`), so a hostile repository
cannot redirect an install. Every `agentcad <command>` named in `AGENTS_BLOCK` is checked against the real parser in
the tests.

## 16. MCP server (`mcp_server.py`)

`agentcad mcp [--root DIR]` serves the CLI as MCP tools on stdio. Each tool builds an argv and runs `cli.main` in
the same process, so a tool result is the command's own output without the per-call import cost, and its tools
mirror the commands: `api`, `new_project`, `build`, `check`, `render`, `drawing`, `measure`, `interfere`,
`overhang`, `wall`, `fit`, `diff`, `printers`, `skill` and `view`. It needs the `mcp` extra. Tools, arguments,
the `--root` restriction and every other detail are in [mcp.md](mcp.md).

## 17. Agent skill

`skills/agentcad/SKILL.md` teaches an agent the loop: scaffold, model, `agentcad check --json --brief`, read FAILs and
their hints, fix, render, look, repeat; `agentcad api` instead of guessing names; how to turn every functional
requirement into a `ctx.expect` or `ctx.slide` check; how to prove a check can fail; the cut-part and drawing
workflows; and the references in `skills/agentcad/references/` (design for printing, cut parts, CadQuery patterns,
verification playbook). `agentcad skill` installs it, and `AGENTS_BLOCK` is its condensed form for hosts without a
skill folder.

## 18. Roadmap after v0.2

- Sliding explode view in the viewer (per-part explode vectors from the manifest).
- Slicer gate (OrcaSlicer / PrusaSlicer CLI) for support volume and print time.
- build123d adapter for `Part.shape`.
- Nesting several cut parts on one sheet.
- Section views and tolerances on drawings.
