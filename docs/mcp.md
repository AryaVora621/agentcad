# MCP server

`agentcad mcp` serves agentcad's commands as tools over stdio, so an agent in Claude Desktop, Claude Code, Cursor or
Codex can build, check, measure, render and draw without shelling out. Renders and drawings come back as images in
the tool result. The process stays warm, so it skips the ~2.5 s CadQuery import on every call: on the bracket example
a cold `agentcad check examples/bracket --json --brief` takes about 3.9 s, the same check through the server about
1.1 s.

## Install

The server needs the `mcp` extra (the plain CLI does not pull in the MCP stack):

```bash
uv tool install 'agentcad[mcp]'
# from a clone, editable:
uv tool install --editable '.[mcp]'
```

Without the extra, `agentcad mcp` exits 2 and prints how to install it.

Find the absolute path of the binary (some hosts do not use your shell `PATH`): `which agentcad`.

## Register it

Claude Code:

```bash
claude mcp add agentcad -- agentcad mcp
# limit it to one folder:
claude mcp add agentcad -- agentcad mcp --root ~/cad
```

Claude Desktop, in `claude_desktop_config.json` (use the absolute path):

```json
{
  "mcpServers": {
    "agentcad": {
      "command": "/Users/you/.local/bin/agentcad",
      "args": ["mcp"]
    }
  }
}
```

Cursor, in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "agentcad": { "command": "agentcad", "args": ["mcp"] }
  }
}
```

Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.agentcad]
command = "agentcad"
args = ["mcp"]
```

## Tools

| Tool | What it does |
|---|---|
| `api` | Every command, ctx helper, config key, gate and frame in one JSON document. Agents call it first. |
| `new_project(dir, template)` | Scaffold a project: `blank`, `bracket` or `box-with-lid`. |
| `build(project)` | Build; returns the measured manifest. |
| `check(project, only, brief)` | Build, run every gate and the project's checks; `brief` (default) lists only what needs attention, with fix hints. |
| `render(inputs, views, frame, section)` | PNG views of a project or files; returns `sheet.png` as an image (long edge at most 1024 px) and the file paths. |
| `drawing(inputs, parts, projection, sheet)` | Dimensioned sheets; returns each as an image (at most 1400 px) and the dimensions as JSON. |
| `measure(files)`, `interfere(files, tol)`, `overhang(files, max_deg, allow, first_layer)`, `wall(files, thin, bands, up, zmin, zmax)`, `fit(files, printer, bed)`, `diff(old, new)`, `printers()` | The file commands, same flags as the CLI. |
| `skill(reference)` | `SKILL.md`, or one reference file (`cadquery-patterns`, `design-for-printing`, `verification-playbook`). |
| `view(inputs)` | Start the live 3D viewer in the server process and return its `http://127.0.0.1:PORT/` URL for the person to open. Calling it again with other inputs restarts it; it stops when the server exits. |

The skill is also exposed as the prompt `agentcad-workflow` and as the resources `agentcad://skill/SKILL.md` and
`agentcad://skill/references/<name>.md`, for hosts without skill support.

Each tool runs the same code path as the command line (`agentcad.cli.main` with `--json`), so results and errors are
identical to the CLI:

- A gate failure (exit 1) is a normal result: the report with `status: FAIL`, `exit_code: 1` and a `hint` per
  failing check.
- A usage or config error (exit 2) and a model that fails to build (exit 3) come back as an MCP error result
  (`isError`) whose text is the `agentcad.error/1` document, or the report with an `ERROR` check for `check`.
- Every call imports `model.py` afresh, so edit the file and call `check` again to see the change.

Loose files for `render` and `drawing` (not a project) write into a fresh temporary folder, named in the result.

## Limiting paths with --root

`agentcad mcp --root DIR` refuses every path argument that resolves (symlinks followed) outside `DIR` with
`PATH is outside --root DIR` as an error result. Without `--root`, relative paths resolve against the folder the
server was started in. `--root` limits what the tools read and write; it is not a sandbox for the Python in a
project's `model.py`, which runs with your permissions like any `agentcad check`.

## Stdout belongs to the protocol

The server speaks JSON-RPC on stdout, and a single stray byte breaks the client. OCCT and VTK can write to
file descriptor 1 from C, and a `model.py` may `print()`. Before serving, the server moves the protocol to a private
copy of descriptor 1 and points descriptor 1 and `sys.stdout` at stderr, so that output lands in the host's server
log instead. Model code can still `print()` for debugging; read it in the host's MCP log, not in the tool result.
The CLI's own output is captured per call and never mixed with it.

Tool calls run one at a time on the main thread. OCCT is not thread safe, and VTK offscreen rendering aborts the
whole process when it runs off the main thread on macOS, so the server does not hand blocking work to a worker thread.
A long build therefore blocks the server's other requests until it finishes.
