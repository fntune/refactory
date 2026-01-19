# Refactory

Token-efficient codebase refactoring plugin for Claude Code. Instead of manually editing imports across dozens of files, Claude calls MCP tools that handle moves atomically.

## Features

- **move_module**: Move files/modules to new locations with automatic import updates
- **move_symbol**: Move functions/classes between modules
- **rename_symbol**: Rename across entire codebase
- **validate_imports**: Check for broken imports after restructuring
- **Dry-run mode**: Preview changes before applying

## Supported Languages

- Python (via Rope)
- TypeScript (via ts-morph)

## Installation

```bash
claude --plugin-dir ~/dev/refactory
```

## Dependencies

Auto-installed on first use:
- Python: `rope`
- TypeScript: `ts-morph` (requires Node.js)

## Usage

### Direct MCP Tool Calls

Claude can call refactoring tools directly:
- `move_module("src/utils.py", "src/core/utils.py")`
- `rename_symbol("src/api.py", "get_data", "fetch_data")`

### Command

`/refactor` - Invoke the refactor-planner agent for complex multi-step reorganizations

## Batch Operations

For multiple refactors, Claude calls tools in parallel:
```
move_module(src/db.py, src/storage/db.py)
move_module(src/cache.py, src/storage/cache.py)
rename_symbol(src/api.py, getData, fetchData)
```

## Components

### MCP Server

Provides tools that Claude calls directly:
- `move_module` - Move file + update imports
- `move_symbol` - Move function/class between files
- `rename_symbol` - Rename across codebase
- `validate_imports` - Check for broken imports

All tools support parallel calls for batch operations.

### Agent

`refactor-planner` - Analyzes codebase structure, creates plans, executes refactoring with dry-run preview.

### Command

`/refactor` - Invokes the refactor-planner agent for complex reorganizations.

### Skill

Provides knowledge about refactoring patterns and best practices.

### Hook

`SessionStart` - Auto-installs dependencies (rope, ts-morph) on first use.

## Manual Dependency Installation

If auto-install fails:

```bash
# Python
pip install rope mcp

# TypeScript (in plugin directory)
cd ~/dev/refactory/server/tsmorph && pnpm install
```

## Testing

Hermetic tests ensure refactoring operations work correctly in isolation.

```bash
# Run all tests
./scripts/test.sh

# Run specific test file
./scripts/test.sh tests/test_python_backend.py

# Run with verbose output
./scripts/test.sh -v

# Run only Python backend tests
./scripts/test.sh -k "python"

# Run only dry-run tests
./scripts/test.sh -k "dry_run"
```

### Test Structure

- `tests/test_python_backend.py` - Rope-based Python refactoring
- `tests/test_typescript_backend.py` - ts-morph TypeScript refactoring
- `tests/test_mcp_server.py` - MCP server integration tests
- `tests/conftest.py` - Shared fixtures (hermetic project creation)

### Requirements

```bash
pip install pytest pytest-asyncio rope mcp
```

TypeScript tests require:
```bash
cd server/tsmorph && pnpm install
```
