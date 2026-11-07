# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

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
- Agent skill (`skills/agentcad`) with design-for-printing, CadQuery and verification references.
