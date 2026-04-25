# refactory

> Codebase refactoring as MCP tools. Move modules, rename symbols, validate imports — atomically, across languages.

```bash
claude --plugin-dir ~/dev/refactory
```

Instead of manually hunting down every import after moving a file, Claude calls `move_module` and gets it done in one tool call.

---

## What it does

Refactory exposes four shared MCP tools plus Python-only Rope extras that Claude Code can call directly during refactoring sessions:

| Tool | Description |
|------|-------------|
| `move_module` | Move a file to a new location, rewrite all imports pointing to it, and optionally overwrite an existing target |
| `move_symbol` | Extract a function, class, or variable to another module, update all references, and fail closed on unsafe moves |
| `rename_symbol` | Rename an identifier across the entire codebase, including parameters when you disambiguate with `line` / `column` if needed |
| `validate_imports` | Run a narrow refactor-safety scan for broken imports and unresolved exported names after restructuring |

Python-only tools:

| Tool | Description |
|------|-------------|
| `organize_imports` | Organize imports in a Python module using Rope |
| `extract_variable` | Extract a selected Python expression into a variable |
| `extract_function` | Extract selected Python statements into a function |
| `inline_symbol` | Inline a selected Python local variable, parameter, or function |

Mutating tools preview by default. Claude must pass `apply: true` to write files. Python previews use Rope's native change descriptions instead of copying the whole repo to a temporary project.

---

## Why MCP tools instead of manual edits

When you ask Claude to move `src/utils.py` to `src/core/utils.py` without tooling, it:
1. Reads dozens of files to find imports
2. Edits each one manually, spending tokens on boilerplate
3. Can miss dynamic imports, re-exports, or barrel files

With refactory, Claude calls one tool:
```
move_module("src/utils.py", "src/core/utils.py")
→ { success: true, affected_files: ["src/api.py", "src/db.py", "tests/test_utils.py", ...] }
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
move_module("src/utils.py", "src/core/utils.py")
→ shows diff: which files change, which imports get rewritten, plus "call again with apply: true"

# Apply it
move_module("src/utils.py", "src/core/utils.py", apply=True)
→ { success: true, affected_files: ["src/api.py", "tests/test_utils.py"] }

# Replace an existing destination explicitly
move_module("src/db.py", "src/storage/db.py", overwrite=True, apply=True)

# Rename an ambiguous parameter by declaration location
rename_symbol("src/api.py", "name", "account_name", line=12, column=19, apply=True)

# Batch: move multiple modules in one turn (parallel tool calls)
move_module("src/db.py", "src/storage/db.py", apply=True)
move_module("src/cache.py", "src/storage/cache.py", apply=True)
rename_symbol("src/api.py", "getData", "fetchData", apply=True)

# Verify nothing broke
validate_imports("src/")
```

For complex multi-step reorganizations, use the `/refactor` command to invoke the refactor-planner agent — it analyzes the codebase structure, drafts a preview plan, and executes stages in order.

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
./scripts/test.sh -k "preview or dry_run" # preview mode only
./scripts/test.sh -v         # verbose
```

Test coverage: `move_module`, `move_symbol`, `rename_symbol`, `validate_imports` — plus preview/apply mode, error cases, and path escaping protection.

## Notes

- `validate_imports` is a refactor-safety probe, not a full compiler or linter run. It stays conservative when a missing exported name cannot be proven statically. The Python scan honors `.gitignore` inside git repos (via `git ls-files`) and skips standard build/venv/cache/worktree paths elsewhere, so ignored and vendored code is never flagged.
- `move_symbol` now fails closed on ambiguous or destructive cases such as same-file moves, target binding collisions, multi-declarator variables, and source-local dependencies that are not safely importable after the move.
- Python `move_module` and `move_symbol` fail closed on two known Rope hazards rather than silently corrupt consumer files:
  - **Basename collision** — if the source file exports a top-level binding with the same name as the module (e.g. `project_service = ProjectService()` inside `project_service.py`), Rope confuses variable attribute access with module attribute access and rewrites `project_service.method()` call sites incorrectly. Rename the binding first, then retry.
  - **Lazy (in-function) imports** — Rope hoists in-function `import` statements to module top, breaking circular-import workarounds. If any consumer imports the moved module/symbol from inside a function, refactory refuses and lists every offending site. Un-lazy those imports (or resolve the underlying circular dependency) first.
- Python refactors accept `expected_git_root` as an optional worktree guard. Use it when running inside linked worktrees: keep `project_root` at the import root (for example `.../worker/backend`) and set `expected_git_root` to the intended git worktree root (for example `.../worker`). Refactory refuses if those resolve to different worktrees.
- Python `move_symbol` previews require the target module to already exist on disk. Rope cannot compute an exact import-rewrite preview against a module it cannot read, and refactory refuses to fabricate or stage one. Create the destination file first (an empty file is fine) or run with `apply: true`.
- `move_symbol` is conservative across barrel / re-export boundaries. If the source is a pure re-export (`export { X } from "./impl"`) or if callers reach the symbol through a re-export chain, refactory refuses rather than guess — an honest refusal beats a silent corruption. A hand-rolled bash/ast-grep chain remains the right tool for those refactors.
