# Arena MCP

Arena MCP is a read-only Model Context Protocol (MCP) server and command-line
tool for inspecting Rockwell Automation Arena simulation models. It extracts
model structure and results into JSON for analysis and downstream tooling,
without saving, running, or modifying the original model.

## Requirements

- Windows with Python 3.10 or later.
- A licensed local Arena installation registered as `Arena.Application`.
- An MCP-compatible client for agent integration; the CLI also works independently.

Platform-independent unit tests can run on Linux without Arena, but live COM
extraction requires Windows and Arena.

## Installation

From the repository directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\arena-mcp.exe doctor
```

You can activate the environment with `.\.venv\Scripts\Activate.ps1` and use
`arena-mcp` directly. Activation is not required when using the full executable
path. See [Development](#development) for test dependencies.

## Configure model access

By default, the server searches existing Documents and Desktop folders under
your home directory, including OneDrive equivalents, and Arena's public
Documents folder. Model and results paths must be within an allowed root.

To replace those defaults, set `ARENA_MODEL_ROOTS` to existing directories
separated by semicolons on Windows:

```powershell
$env:ARENA_MODEL_ROOTS = "C:\Arena\Models;C:\Shared\Arena"
.\.venv\Scripts\arena-mcp.exe doctor --live-check
```

`doctor` reports platform, Arena registration, and available roots; the optional
live check also connects to Arena. A failed check returns a nonzero exit code.
The shell assignment above lasts only for that shell and processes it launches.
Set the variable in Windows environment settings and restart the client, or
configure it explicitly for the MCP server as shown below.

`ARENA_ALLOW_ANY_PATH=1` disables the allowed-root restriction. Use it only when
you trust the client and intend to permit access to supported files anywhere the
server process can read.

## Connect an MCP client

Install the project first. In the examples below, replace
`C:\path\to\arena-mcp` with the **absolute path** to your checkout and
`C:\Arena\Models` with an existing allowed model folder. Keep the executable on
the same Windows machine as Arena; a remote agent or container cannot use its
local COM installation automatically. Merge examples into existing configuration
instead of replacing other servers.

These examples start the server over stdio, its default transport. Once
connected, ask the client to call `arena_status` with `live_check: true`, then
use `extract_arena_report` on a model inside an allowed root.

### Abacus.AI

For Abacus Desktop (Code Mode), its CLI, or its VS Code extension, add this to
`~/.abacusai/mcp.json` (or the project's `.abacusai/mcp.json`):

```json
{
  "mcpServers": {
    "arena": {
      "command": "C:\\path\\to\\arena-mcp\\.venv\\Scripts\\arena-mcp.exe",
      "args": [],
      "env": {
        "ARENA_MODEL_ROOTS": "C:\\Arena\\Models"
      }
    }
  }
}
```

Reload the client after editing the file. Use `/mcp` to check the connection, or
manage it in **Abacus Settings > MCP servers** in VS Code. See the
[Abacus MCP documentation](https://abacus.ai/help/abacusai-desktop/mcp-configuration).

### Claude Code

Register the server from PowerShell:

```powershell
claude mcp add --env 'ARENA_MODEL_ROOTS=C:\Arena\Models' --transport stdio arena -- 'C:\path\to\arena-mcp\.venv\Scripts\arena-mcp.exe'
```

Use `claude mcp list` or `/mcp` in Claude Code to check it. See
[Claude Code's MCP documentation](https://code.claude.com/docs/en/mcp).

### Codex

Register the server from PowerShell:

```powershell
codex mcp add arena --env 'ARENA_MODEL_ROOTS=C:\Arena\Models' -- 'C:\path\to\arena-mcp\.venv\Scripts\arena-mcp.exe'
codex mcp list
```

Codex stores the configuration in `~/.codex/config.toml` by default; local Codex
CLI and IDE clients on the same host share it. See the
[Codex MCP documentation](https://developers.openai.com/codex/mcp/).

### Cursor

Add the same `mcpServers` JSON shown in [Abacus.AI](#abacusai) to
`~/.cursor/mcp.json` for your user, or `.cursor/mcp.json` for the project.
Add `"type": "stdio"` inside the `arena` entry, replace the example paths, and
check the server in Cursor's MCP settings. See the
[Cursor MCP documentation](https://cursor.com/docs/mcp).

### Gemini CLI

Add the same `mcpServers` JSON shown in [Abacus.AI](#abacusai) to
`~/.gemini/settings.json` for your user, or `.gemini/settings.json` for the
project. Replace the example paths and use `/mcp` in Gemini CLI to check the
connection. See the [Gemini CLI MCP documentation](https://geminicli.com/docs/tools/mcp-server/).

### GitHub Copilot

For the Copilot harness in VS Code, add the following to
`~/.copilot/mcp-config.json` (or the workspace-root `.mcp.json`):

```json
{
  "mcpServers": {
    "arena": {
      "type": "stdio",
      "command": "C:\\path\\to\\arena-mcp\\.venv\\Scripts\\arena-mcp.exe",
      "args": [],
      "env": {
        "ARENA_MODEL_ROOTS": "C:\\Arena\\Models"
      },
      "tools": ["*"]
    }
  }
}
```

Alternatively, open **Chat: Open Customizations > MCP Servers**, add a
**Command (stdio)** server, and select the desired configuration scope. Verify
the connection by asking Copilot to call `arena_status`. See the
[VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## CLI usage

With the virtual environment activated, use:

```powershell
arena-mcp --status
arena-mcp --list-models
arena-mcp --inspect 'C:\Arena\Models\MyModel.doe'
arena-mcp --extract 'C:\Arena\Models\MyModel.doe'
arena-mcp --audit 'C:\Arena\Models\MyModel.doe'
arena-mcp --report 'C:\Arena\Models\MyModel.doe' --output report.json
```

`--report` combines the audit and neutral IR in one primary Arena session and
includes module counts, truncation state, warnings, and translation readiness.
Optional SIMAN extraction opens an isolated temporary copy separately.

- `--max-modules` and `--max-repeat-rows` apply to `--extract`, `--audit`, and
  `--report`.
- `--include-vba-source`, `--include-siman-source`, and
  `--include-binary-payloads` apply to `--audit` and `--report`.
- `--output PATH` writes JSON to a **new** file and refuses to overwrite an
  existing file. Without it, JSON is written to stdout.
- `arena-mcp --help` lists all options. Running `arena-mcp` without a CLI
  operation starts the stdio MCP server.

## MCP tools

All tools are annotated read-only.

| Tool | Purpose |
| --- | --- |
| `arena_status` | Check registration and optionally connect to Arena |
| `list_arena_models` | Discover `.doe` models in allowed roots |
| `inspect_arena_model` | Summarize model metadata, modules, and compatibility |
| `list_arena_modules` | List modules and their operands |
| `list_arena_connections` | List the model's connection graph |
| `extract_arena_model` | Extract the neutral intermediate representation (IR) |
| `extract_arena_report` | Return the audit and IR together |
| `analyze_arena_model_compatibility` | Classify module definitions for translation |
| `audit_arena_model_data` | Report data coverage and translation blockers |
| `inspect_arena_project_bar` | Inspect attached panels and operand definitions |
| `extract_arena_submodels` | Extract submodel hierarchies |
| `extract_arena_visual_model` | Extract layout and animation data |
| `extract_arena_material_handling` | Extract material-handling collections |
| `inspect_arena_compound_file` | Inspect OLE compound-file streams |
| `extract_arena_siman_source` | Generate SIMAN source from a temporary copy |
| `inspect_arena_results` | Inspect an Arena SQLite results database |
| `read_arena_results` | Read a supported statistics view |

Use `extract_arena_report` as the usual starting point after checking status.
Review its `completeness` and `audit.coverage_report` fields before translating;
warnings, limits, and coverage gaps can make the extracted data incomplete.

## Output contracts

- IR schema version: `0.3.0`
- Audit schema version: `2.0.0`
- Combined report schema version: `1.0.0`

The [combined-report JSON Schema](arena_mcp/schemas/arena-report.schema.json)
validates the report envelope and key nested fields; it is not a complete schema
for every IR and audit field. The
[example report](arena_mcp/examples/report.example.json) is synthetic, not an
Arena extraction. Individual tools return their relevant version fields where
defined; not every response has both schema versions.

## Troubleshooting

- **Arena is not registered or reachable:** run `arena-mcp doctor --live-check`
  on the Windows machine, then check the Arena installation, license, and COM
  registration.
- **Path is outside allowed roots:** use an existing directory in
  `ARENA_MODEL_ROOTS`, ensure the client passes it to the server, and restart or
  reconnect the server. `arena-mcp --status` shows the resolved roots.
- **Server does not start:** check that the configured executable path points to
  the virtual environment created during installation and that the client runs on
  the Windows host, not a remote environment.
- **Request hangs:** lock acquisition and collection-readiness waits are bounded,
  but a COM method already blocked inside Arena cannot currently be canceled
  safely by a client timeout.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m unittest discover -v
```

The unit tests also run on Linux using an equivalent Python environment. CI
tests Windows and Linux with Python 3.10 and 3.12; it does not run live Arena
integration tests. Tests are grouped by feature in [tests/](tests/). Run a
specific group with, for example, `python -m unittest -v tests.test_model`.
See [AGENTS.md](AGENTS.md) for coding-agent guidance.

## Project layout

- `arena_api.py`: shared API facade; use the `arena-mcp` command-line entry
  point for CLI and server operation.
- `arena_mcp/cli.py`, `server.py`: CLI and MCP adapters.
- `arena_mcp/com.py`, `paths.py`, `environment.py`: COM sessions, allowed paths,
  status, and discovery.
- `arena_mcp/model.py`, `audit.py`, `reports.py`, and related modules: extraction,
  coverage audits, and combined reports.
- `arena_mcp/artifacts.py`, `results.py`: compound/SIMAN artifacts and read-only
  SQLite results.
- `arena_mcp/schemas/`, `examples/`: report schema and synthetic fixture.

## License and disclaimer

The original written material in this repository is licensed under MIT;
see [LICENSE](LICENSE) for attribution requirements and third-party material.
This project is not affiliated with or endorsed by Rockwell Automation. Arena
is their trademark, and a licensed installation is required.
