# Contributing

Thanks for helping. agentcad exists so that agents verify geometry by measuring it, so the bar for every change is
the same: a check is only as good as a fixture that makes it fail.

## Development setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,mcp]"
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
- Every FAIL or WARN a gate can produce carries a `hint` naming the numbers and the `Part` or config field to change.
- Update `docs/ARCHITECTURE.md` in the same change when you change a public function, CLI option or config key.
- A new command, flag, config key or gate also goes into `docs/cli.md`, the skill (`skills/agentcad/SKILL.md` and its
  references) and `AGENTS_BLOCK` in `src/agentcad/skill_install.py` when an agent needs it. `agentcad api` reads the
  code, so check its output after the change. Keep `SKILL.md` near 200 lines: agents load it into context, so put
  detail in `references/`.
- Run every command you document at least once and paste its real output.

## Reporting bugs

Open an issue with the `agentcad doctor --json` output, the smallest `agentcad.toml` and `model.py` that reproduce
the problem, and the report you expected versus the one you got.

## License of contributions

agentcad is offered under the PolyForm Noncommercial License 1.0.0 and, separately, under paid commercial licenses.
By submitting a contribution you confirm that you wrote it or have the right to submit it, and you grant the author
a perpetual, worldwide, royalty-free license to use, modify and relicense it as part of agentcad under both of those
models. Say so in the pull request if you cannot agree to this before you open it.

## Changing the agent skill

Run the fresh-agent evals in [evals/](evals/README.md) before and after a skill change, and turn every friction
item they report into a fix (code with a test, or skill text).
