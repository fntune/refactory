---
name: Refactoring Patterns
description: This skill should be used when discussing codebase reorganization patterns, module structure best practices, or planning how to restructure Python/TypeScript projects. Use when user asks about "how to organize code", "module structure", "when to split files", or "refactoring strategies".
version: 1.0.0
---

# Codebase Refactoring Patterns

Guide for reorganizing code by responsibility while maintaining clean imports.

## Module Organization Principles

### Responsibility-Based Structure

Organize by what code does, not what it is:

```
project/
├── api/           # External interfaces (REST, GraphQL, CLI)
├── core/          # Business logic, domain models
├── storage/       # Data persistence (DB, cache, files)
├── integrations/  # Third-party service clients
└── utils/         # Shared utilities (logging, config)
```

### When to Split a Module

Split when:
- File exceeds 500 lines
- Multiple unrelated responsibilities
- Circular import risks
- Different change frequencies

Keep together when:
- High cohesion (always change together)
- Shared internal state
- Complex initialization order

## Python Refactoring Patterns

### Moving Modules

Use `move_module` MCP tool. Creates `__init__.py` files automatically.

```json
{"source": "src/db.py", "target": "src/storage/db.py"}
```

After move, re-export from package `__init__.py` for backward compatibility:
```python
# src/storage/__init__.py
from src.storage.db import Database, connect
```

### Moving Symbols

Use `move_symbol` for functions/classes between modules:

```json
{
  "source_file": "src/utils.py",
  "symbol_name": "parse_config",
  "target_file": "src/config/parser.py"
}
```

### Circular Import Prevention

Before moving, check for cycles:
1. A imports B, B imports A → cycle
2. Solution: Extract shared code to new module C

## TypeScript Refactoring Patterns

### Barrel Exports

After moving, update index.ts:
```typescript
// src/storage/index.ts
export * from "./db";
export * from "./cache";
```

### Path Aliases

Configure tsconfig.json for clean imports:
```json
{
  "compilerOptions": {
    "paths": {
      "@storage/*": ["src/storage/*"],
      "@core/*": ["src/core/*"]
    }
  }
}
```

## Batch Operations

Call multiple MCP tools in parallel for batch refactoring:

```
# Execute multiple moves in parallel
move_module(src/db.py → src/storage/db.py)
move_module(src/cache.py → src/storage/cache.py)
rename_symbol(src/api.py, getData → fetchData)
```

## Validation Checklist

After refactoring:
1. Run `validate_imports` tool
2. Run test suite
3. Check for unused imports
4. Verify __init__.py exports
5. Update documentation paths

## Common Mistakes

❌ **Don't:**
- Move without checking dependents first
- Forget __init__.py in new packages
- Leave stale imports
- Break backward compatibility without deprecation

✅ **Do:**
- Preview with dry_run=true first
- Re-export from package root if public API
- Update all relative imports
- Add deprecation warnings for moved public APIs
