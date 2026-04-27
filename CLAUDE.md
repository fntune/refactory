# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Refactory** is an MCP (Model Context Protocol) plugin that provides token-efficient codebase refactoring tools. Instead of manually editing imports across dozens of files, Claude calls specialized tools that handle refactoring operations atomically.

The plugin supports both Python (via Rope library) and TypeScript (via ts-morph).

## Development Commands

### Setup & Installation

The plugin auto-installs dependencies on first use via the SessionStart hook:
- Python: `rope`, `mcp`
- TypeScript: `ts-morph` (requires Node.js and pnpm/npm)

Manual installation (if needed):
```bash
# Python dependencies
pip install rope mcp

# TypeScript dependencies
cd server/tsmorph && pnpm install
```

### Running Tests

```bash
# Run all tests
./scripts/test.sh

# Run specific test file
./scripts/test.sh tests/test_python_backend.py

# Run with verbose output
./scripts/test.sh -v

# Run specific test pattern
./scripts/test.sh -k "dry_run"

# Python only
./scripts/test.sh -k "python"

# TypeScript only
./scripts/test.sh -k "typescript"
```

The test suite uses hermetic fixtures (`tests/conftest.py`) that create isolated temporary projects for each test.

## Architecture

### Core Components

**MCP Server** (`server/main.py`)
- Entry point that implements the MCP protocol
- Routes tool calls to appropriate language backends
- Handles language detection and validation
- Returns structured JSON responses

**Language Backends**
- `server/backends/python.py`: Uses Rope library for Python refactoring
  - `move_module()`: Move files and update imports
  - `move_symbol()`: Extract functions/classes to other modules
  - `rename_symbol()`: Rename identifiers across codebase
  - `validate_imports()`: Check for import errors

- `server/backends/typescript.py`: Wraps ts-morph via subprocess
  - Calls `server/tsmorph/refactor.js` with JSON arguments
  - Same four operations as Python backend
  - Returns structured results

**TypeScript Helper** (`server/tsmorph/refactor.js`)
- Node.js script using ts-morph library
- Handles all TypeScript/JavaScript refactoring operations
- Executes as subprocess from Python backend

### Tool Interface (What Claude Calls)

All tools accept:
- `project_root` (required absolute path)
- `apply` (optional, defaults to preview mode unless exactly `true`)
- Language-specific parameters

Tools return structured JSON with operation summary and affected files list.

### Extensions & Configuration

**Hooks** (`hooks/`)
- `hooks.json`: Triggers dependency installation on SessionStart
- `install-deps.sh`: Auto-installs rope, mcp, and ts-morph on first use

**Commands** (`commands/`)
- `refactor.md`: CLI command `/refactor` that invokes the refactor-planner agent

**Skills** (`skills/`)
- `refactoring/SKILL.md`: Knowledge about module organization patterns and refactoring strategies

## Data Flow

1. Claude calls MCP tool with file paths and operation parameters
2. `main.py` detects language from file extension
3. Backend instantiated and method called with parsed arguments
4. **Python**: Uses Rope library directly to modify AST and imports
5. **TypeScript**: Spawns subprocess to run `refactor.js`
6. Result serialized as JSON and returned to Claude
7. Claude displays result or performs follow-up operations

## Key Technical Details

### Path Handling
- All paths are relative to project root
- Backends validate that resolved paths stay within project root (security measure)
- Dry-run mode temporarily creates directories to compute changes without persisting

### Python Backend (Rope)
- Uses AST parsing to find symbol offsets for move_symbol operations
- Automatically creates `__init__.py` files in target directories
- Rope's refactoring engines handle import rewriting
- Validates symbol existence before operations

### TypeScript Backend
- Communicates via JSON passed through subprocess
- Parameter names converted to camelCase for JavaScript convention
- Node process timeout set to 120 seconds
- ts-morph handles path alias resolution and import rewriting

### Dry-Run Behavior
- Python: Creates temporary directories, computes changes, then reverts
- TypeScript: ts-morph script handles dry-run flag
- Useful for previewing changes before committing

## Testing Strategy

**Test Organization**
- `tests/test_python_backend.py`: Rope-based operations in isolation
- `tests/test_typescript_backend.py`: ts-morph via subprocess
- `tests/test_mcp_server.py`: Tool routing and error handling
- `tests/conftest.py`: Fixtures that create temporary projects with test files

**Fixture Pattern**
- Creates isolated `tmp_project` directories for each test
- Populates with sample Python/TypeScript files
- Tests can move/rename without affecting other tests
- Cleanup handled automatically by pytest

**Test Coverage**
- `move_module`: File relocation with import updates, dry-run mode
- `move_symbol`: Function/class extraction, circular import detection
- `rename_symbol`: Identifier renaming across multiple files
- `validate_imports`: Detection of broken imports
- Error cases: Invalid paths, missing symbols, escaping project root

## Important Implementation Notes

### Circular Dependencies
- Python backend includes checks to prevent circular import creation during move_symbol
- Detected by analyzing import graphs before and after operation

### Path Escaping Protection
- Both backends validate that paths don't escape project root using `resolve()` and `is_relative_to()`
- Prevents malicious or accidental path traversal

### Import Rewriting Strategy
- Python: Rope's refactoring engines automatically update relative and absolute imports
- TypeScript: ts-morph updates both ES6 and CommonJS import statements
- Both handle re-exports and barrel files

### Return Format
All operations return JSON with structure:
```json
{
  "success": true/false,
  "message": "Human-readable summary",
  "changedFiles": ["path/to/file.py", "path/to/other.ts"],
  "preview": "Detailed diff for dry-run mode"
}
```

## Known Limitations

- Circular dependencies can prevent some move_symbol operations
- Path aliases in TypeScript must be properly configured in tsconfig.json
- Large codebases may be slow due to Rope/ts-morph analysis overhead
- Dry-run mode requires temporary filesystem modifications

## References

- [Rope Documentation](https://rope.readthedocs.io/)
- [ts-morph Documentation](https://ts-morph.readthedocs.io/)
- [MCP Protocol](https://modelcontextprotocol.io/)
