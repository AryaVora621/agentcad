# Contributing

Thanks for helping. agentcad exists so that agents verify geometry by measuring it, so the bar for every change is
the same: a check is only as good as a fixture that makes it fail.

## Development setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests examples
```

## Rules for changes

- Every number a check reports comes from the built solid or the exported mesh, never from model parameters.
- A new built-in gate ships with a passing fixture and a failing fixture, and the failing one is asserted on its
  status and measured values, not only on "something went wrong".
- Keep `--json` output backward compatible within a schema version. Changing a field means a new schema version and
  a CHANGELOG entry.
- Keep the CLI headless and deterministic: seed random sampling, no network access, no GUI outside `agentcad view`.
- Update `docs/ARCHITECTURE.md` in the same change when you change a public function, CLI option or config key.

## Reporting bugs

Open an issue with the `agentcad doctor --json` output, the smallest `agentcad.toml` and `model.py` that reproduce
the problem, and the report you expected versus the one you got.
