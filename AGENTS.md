# Agent instructions

These instructions apply to Codex, Abacus.AI, Copilot, and other coding agents.

## Project

- Python 3.10+ read-only MCP server and CLI for Rockwell Automation Arena models.
- Arena COM extraction requires Windows, pywin32, and a licensed Arena installation.
  Linux can run the platform-independent unit tests; do not claim live Arena
  behavior was tested without a licensed Windows environment.
- Never save, run, or edit the original model. SIMAN generation uses a temporary
  copy; preserve the path restrictions and read-only database access.

## Layout

- `arena_api.py` is the shared API facade; `arena-mcp` is the CLI and server
  entry point.
- `arena_mcp/cli.py` and `arena_mcp/server.py` provide the CLI and MCP adapters.
- `arena_mcp/com.py` manages COM sessions; `model.py`, `audit.py`, `reports.py`,
  and their neighboring modules handle extraction and audits.
- `arena_mcp/schemas/` and `arena_mcp/examples/` contain the combined-report
  contract and synthetic example. Respect existing IR and audit schema versions;
  update contracts, examples, and documentation when output changes.
- `tests/` contains unit tests grouped by feature; `pyproject.toml` defines
  dependencies and packaging.

## Working and validation

- Make focused changes, preserve existing behavior and uncommitted user work,
  and add tests for changed behavior. Surface extraction errors and truncation.
- Use `python -m pip install -e ".[test]"` to set up a development environment
  when dependencies are missing.
- Run `python -m unittest discover -v` after code changes. For focused iteration,
  use `python -m unittest -v tests.test_model.ModelTests.<test_name>`.
- CI runs the unit tests on Windows and Linux with Python 3.10 and 3.12. Document
  any skipped or unavailable checks; do not commit generated output, local Arena
  models, credentials, or environment-specific paths.

## Code review

- Flag changes that could modify source models, break legacy entry points, lose
  extraction warnings, or report incomplete output as complete.
