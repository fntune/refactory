# refactory

> Codebase refactoring as MCP tools. Move modules, rename symbols, validate imports — atomically, across languages.

```bash
claude --plugin-dir ~/dev/refactory
```

Instead of manually hunting down every import after moving a file, Claude calls `move_module` and gets it done in one tool call.

---

## What it does

Refactory exposes four MCP tools that Claude Code can call directly during refactoring sessions:

| Tool | Description |
|------|-------------|
| `move_module` | Move a file to a new location, rewrite all imports pointing to it |
| `move_symbol` | Extract a function or class to another module, update all references |
| `rename_symbol` | Rename an identifier across the entire codebase |
| `validate_imports` | Scan for broken imports after restructuring |

Every tool supports **dry-run mode** — Claude can preview the full diff before committing any changes.

---

## Why MCP tools instead of manual edits

When you ask Claude to move `src/utils.py` to `src/core/utils.py` without tooling, it:
1. Reads dozens of files to find imports
2. Edits each one manually, spending tokens on boilerplate
3. Can miss dynamic imports, re-exports, or barrel files

With refactory, Claude calls one tool:
```
move_module("src/utils.py", "src/core/utils.py")
→ { success: true, changedFiles: ["src/api.py", "src/db.py", "tests/test_utils.py", ...] }
```

Batch operations run in parallel — move three modules in the same turn.

---

## Languages

**Python** — via [Rope](https://rope.readthedocs.io/)
- AST-based symbol location (handles decorators, nested classes)
- Automatic `__init__.py` creation in target directories
- Circular import detection before `move_symbol`
- Rope's refactoring engines rewrite relative and absolute imports

**TypeScript** — via [ts-morph](https://ts-morph.com/)
- Respects `tsconfig.json` path aliases
- Updates ES6 and CommonJS imports
- Handles barrel files and re-exports
- Monorepo-aware

Language is detected from file extension. Both backends validate that resolved paths stay within the project root.

---

## Installation

```bash
claude --plugin-dir ~/dev/refactory
```

Dependencies (rope, ts-morph) are auto-installed on first session start via the `SessionStart` hook.

Manual install if needed:

```bash
pip install rope mcp
cd server/tsmorph && pnpm install
```

---

## Usage

Claude calls tools directly during a session. Typical flow:

```
# Preview a move
move_module("src/utils.py", "src/core/utils.py", dry_run=True)
→ shows diff: which files change, which imports get rewritten

# Apply it
move_module("src/utils.py", "src/core/utils.py")
→ { success: true, changedFiles: ["src/api.py", "tests/test_utils.py"] }

# Batch: move multiple modules in one turn (parallel tool calls)
move_module("src/db.py", "src/storage/db.py")
move_module("src/cache.py", "src/storage/cache.py")
rename_symbol("src/api.py", "getData", "fetchData")

# Verify nothing broke
validate_imports("src/")
```

For complex multi-step reorganizations, use the `/refactor` command to invoke the refactor-planner agent — it analyzes the codebase structure, drafts a plan with dry-run previews, and executes stages in order.

---

## Components

```
server/
├── main.py               MCP server, tool routing, language detection
├── backends/
│   ├── python.py         Rope-based Python refactoring
│   └── typescript.py     ts-morph subprocess wrapper
└── tsmorph/
    └── refactor.js       Node.js script for TypeScript operations

hooks/
└── hooks.json            SessionStart: auto-install dependencies

commands/
└── refactor.md           /refactor — invokes refactor-planner agent

skills/
└── refactoring/          Refactoring patterns knowledge base
```

---

## Testing

Tests use hermetic fixtures — each test gets an isolated temporary project, so operations can't interfere with each other.

```bash
./scripts/test.sh            # all tests
./scripts/test.sh -k python  # Python backend only
./scripts/test.sh -k dry_run # dry-run mode only
./scripts/test.sh -v         # verbose
```

Test coverage: `move_module`, `move_symbol`, `rename_symbol`, `validate_imports` — plus dry-run, error cases, and path escaping protection.
