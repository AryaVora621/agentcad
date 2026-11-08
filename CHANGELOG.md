# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - unreleased

### Added
- Brief reports: `agentcad check --json --brief` keeps only the checks that need attention and groups the passing ones
  by id. On `examples/broken` the report shrinks from 17,895 to 3,144 bytes (about 17.9 KB to 3.1 KB); an all-pass
  project is under 1 KB.
- Fix hints: every built-in gate adds a `hint` to a FAIL or WARN (the numbers and the `Part` or config field to
  change), and `ctx.expect(..., hint=...)` takes one for project checks. The text table prints it under the row.
- `agentcad api`: every command, `CheckContext` helper, config key with its default, gate id, printer profile and
  frame in one document (`--json` for machines).
- `agentcad skill install|show|path` for Claude Code, Codex, Cursor and AGENTS.md (`--host`, `--project`, `--link`,
  `--force`), with a guard that refuses to write through a symlink inside a project. The skill ships in the wheel.
- 2D cut parts for laser, CNC router and waterjet: `Part(..., cut="laser"|"cnc"|"waterjet")`, a `[flat]` config
  section, DXF and SVG profile export (`build/dxf`, `build/svg`, a `flat` record in the manifest), optional kerf
  compensation, and five gates: `flat_profile`, `sheet_fit`, `min_feature`, `inner_corners`, `hole_size`. New example
  `examples/flat_panel`. Parts with `cut` set skip the 3D-printing gates.
- `agentcad drawing`: dimensioned SVG, PDF and PNG sheets from the built solids (third or first angle, standard
  scales, hole callouts and positions, title block, byte-identical output for the same input).
- MCP server (`agentcad mcp`, extra `agentcad[mcp]`): 15 tools mirroring the CLI, renders and drawings returned as
  images, the skill as a prompt and resources, `--root` confinement. CadQuery stays loaded, so a repeat `check` of
  examples/bracket runs about 3.5 to 4 times faster than a cold CLI run (1.1 s against 3.9 s on a quiet development
  Mac; 2.7 s against 10.7 s while other jobs loaded it). See
  [docs/mcp.md](docs/mcp.md).
- `llms.txt` for agents that read a project index.

### Changed
- The agent skill leads with the brief loop, `agentcad api` and the cut-part and drawing workflows, and gains
  `references/cut-parts.md`. The AGENTS.md block gains one line each for cut parts and drawings.
- `Report` JSON: the `agentcad.report/1` schema is unchanged; `hint` is an optional check field and `view` an
  optional top-level field.
- README, CLI reference, getting started and architecture documents cover all of the above.

### Security
- `agentcad skill install` refuses to write through a symlink planted inside the target project, so a hostile
  repository cannot redirect an install onto files elsewhere on disk.

## [0.1.0] - unreleased

### Added
- Licensing: PolyForm Noncommercial 1.0.0 for noncommercial use, paid commercial licenses on request.
- Project contract: `agentcad.toml` plus a Python model module with `parts()` and optional `checks(ctx)`.
- Build pipeline with atomic output: STEP per part, print-frame and use-frame STL, assembly STEP, `manifest.json`.
- Built-in gates measured from the built geometry: validity, single solid, watertight, bed fit, bed contact,
  overhang in the print frame, minimum wall thickness, pairwise interference in the use frame.
- Project checks with probe solids and motion tests (`ctx.slide`, `ctx.play`), sections and distances.
- JSON reports (`agentcad.report/1`) and exit codes for agents; compact tables for people.
- Standalone file commands: `measure`, `interfere`, `overhang`, `wall`, `fit`, `diff`.
- Offscreen VTK renders (multi-view sheet, filled sections) and a local live three.js viewer.
- Viewer safety: loopback-only Host check (blocks DNS rebinding), clear exit-2 errors for a busy `--port` or a
  folder with no meshes, and automatic port selection that skips ports another server already answers on.
- Agent skill (`skills/agentcad`) with design-for-printing, CadQuery and verification references.
