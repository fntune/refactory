#!/usr/bin/env python
"""
Refactory MCP Server - Token-efficient codebase refactoring tools.

Provides move_module, move_symbol, rename_symbol, validate_imports tools
that Claude can call directly instead of manual Edit operations.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from backends.python import PythonBackend
from backends.typescript import TypeScriptBackend

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("refactory")

server = Server("refactory")

BACKENDS = {
    "python": PythonBackend,
    "typescript": TypeScriptBackend,
}

EXTENSION_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
}


def detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    lang = EXTENSION_MAP.get(ext)
    if not lang:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {list(EXTENSION_MAP.keys())}")
    return lang


def get_backend(language: str):
    """Get refactoring backend for language."""
    backend_cls = BACKENDS.get(language)
    if not backend_cls:
        raise ValueError(f"Unsupported language: {language}. Supported: {list(BACKENDS.keys())}")
    return backend_cls()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available refactoring tools."""
    return [
        Tool(
            name="move_module",
            description="Move a file/module to a new location and update all imports across the codebase. Supports Python and TypeScript.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Current path of the file to move (relative to project root)",
                    },
                    "target": {
                        "type": "string",
                        "description": "New path for the file (relative to project root)",
                    },
                    "project_root": {
                        "type": "string",
                        "description": "Root directory of the project (defaults to cwd)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying them",
                        "default": False,
                    },
                },
                "required": ["source", "target"],
            },
        ),
        Tool(
            name="move_symbol",
            description="Move a function, class, or variable from one module to another and update all references.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_file": {
                        "type": "string",
                        "description": "File containing the symbol to move",
                    },
                    "symbol_name": {
                        "type": "string",
                        "description": "Name of the function/class/variable to move",
                    },
                    "target_file": {
                        "type": "string",
                        "description": "Destination file for the symbol",
                    },
                    "project_root": {
                        "type": "string",
                        "description": "Root directory of the project (defaults to cwd)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying them",
                        "default": False,
                    },
                },
                "required": ["source_file", "symbol_name", "target_file"],
            },
        ),
        Tool(
            name="rename_symbol",
            description="Rename a function, class, variable, or parameter across the entire codebase.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "File containing the symbol to rename",
                    },
                    "old_name": {
                        "type": "string",
                        "description": "Current name of the symbol",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "New name for the symbol",
                    },
                    "project_root": {
                        "type": "string",
                        "description": "Root directory of the project (defaults to cwd)",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Preview changes without applying them",
                        "default": False,
                    },
                },
                "required": ["file", "old_name", "new_name"],
            },
        ),
        Tool(
            name="validate_imports",
            description="Check for broken imports in the codebase after refactoring. Returns list of files with import errors.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_root": {
                        "type": "string",
                        "description": "Root directory of the project (defaults to cwd)",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["python", "typescript"],
                        "description": "Language to validate (validates all if not specified)",
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a refactoring tool."""
    try:
        project_root = arguments.get("project_root", ".")
        dry_run = arguments.get("dry_run", False)

        if name == "move_module":
            source = arguments["source"]
            target = arguments["target"]
            language = detect_language(source)
            backend = get_backend(language)
            result = backend.move_module(source, target, project_root, dry_run)

        elif name == "move_symbol":
            source_file = arguments["source_file"]
            symbol_name = arguments["symbol_name"]
            target_file = arguments["target_file"]
            language = detect_language(source_file)
            backend = get_backend(language)
            result = backend.move_symbol(source_file, symbol_name, target_file, project_root, dry_run)

        elif name == "rename_symbol":
            file = arguments["file"]
            old_name = arguments["old_name"]
            new_name = arguments["new_name"]
            language = detect_language(file)
            backend = get_backend(language)
            result = backend.rename_symbol(file, old_name, new_name, project_root, dry_run)

        elif name == "validate_imports":
            language = arguments.get("language")
            results = []
            languages = [language] if language else list(BACKENDS.keys())
            for lang in languages:
                backend = get_backend(lang)
                lang_result = backend.validate_imports(project_root)
                if lang_result:
                    results.append({"language": lang, "errors": lang_result})
            result = {"valid": len(results) == 0, "issues": results}

        else:
            raise ValueError(f"Unknown tool: {name}")

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
