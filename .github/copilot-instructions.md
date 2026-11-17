# Repository instructions for Copilot

Read and follow the repository-wide guidance in [AGENTS.md](../AGENTS.md).

- This is a read-only Arena MCP server and CLI. Never save, run, or edit the
  original model; preserve allowed-root checks and read-only database access.
- Use the focused modules in `arena_mcp/` and `arena_api.py`; `arena-mcp` is the
  CLI and server entry point.
- Run `python -m unittest discover -v` for code changes. Install test
  dependencies with `python -m pip install -e ".[test]"` if needed.
- Real COM extraction requires licensed Arena on Windows; distinguish mocked
  or Linux tests from live integration verification.
- Keep schemas, examples, warnings, and documentation consistent with output
  changes, and preserve uncommitted user changes.
