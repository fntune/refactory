#!/usr/bin/env python
"""
Refactory MCP Server - Token-efficient codebase refactoring tools.

Provides shared cross-language tools plus Python-only Rope extras.
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
from validation import validate_position_selector

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
                    "overwrite": {
                        "type": "boolean",
                        "description": "Overwrite an existing target file if it already exists",
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
                    "line": {
                        "type": "integer",
                        "description": "1-based declaration line for disambiguating parameter renames",
                    },
                    "column": {
                        "type": "integer",
                        "description": "1-based declaration column for disambiguating parameter renames",
                    },
                },
                "required": ["file", "old_name", "new_name"],
            },
        ),
        Tool(
            name="validate_imports",
            description="Check for refactor-related import and name-resolution errors after restructuring. This is a narrow safety probe, not a full compiler or linter run.",
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
        Tool(
            name="organize_imports",
            description="Python-only: organize imports in a Python module using Rope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Python file whose imports should be organized",
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
                "required": ["file"],
            },
        ),
        Tool(
            name="extract_variable",
            description="Python-only: extract the selected expression into a variable using Rope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Python file containing the selection"},
                    "new_name": {"type": "string", "description": "Name of the extracted variable"},
                    "start_line": {"type": "integer", "description": "1-based start line"},
                    "start_column": {"type": "integer", "description": "1-based start column"},
                    "end_line": {"type": "integer", "description": "1-based end line"},
                    "end_column": {
                        "type": "integer",
                        "description": "1-based end column (exclusive)",
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
                "required": [
                    "file",
                    "new_name",
                    "start_line",
                    "start_column",
                    "end_line",
                    "end_column",
                ],
            },
        ),
        Tool(
            name="extract_function",
            description="Python-only: extract the selected statements into a function using Rope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Python file containing the selection"},
                    "new_name": {"type": "string", "description": "Name of the extracted function"},
                    "start_line": {"type": "integer", "description": "1-based start line"},
                    "start_column": {"type": "integer", "description": "1-based start column"},
                    "end_line": {"type": "integer", "description": "1-based end line"},
                    "end_column": {
                        "type": "integer",
                        "description": "1-based end column (exclusive)",
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
                "required": [
                    "file",
                    "new_name",
                    "start_line",
                    "start_column",
                    "end_line",
                    "end_column",
                ],
            },
        ),
        Tool(
            name="inline_symbol",
            description="Python-only: inline a selected local variable, parameter, or function using Rope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Python file containing the symbol reference"},
                    "line": {"type": "integer", "description": "1-based line containing the symbol"},
                    "column": {"type": "integer", "description": "1-based column containing the symbol"},
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
                "required": ["file", "line", "column"],
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
            overwrite = arguments.get("overwrite", False)
            language = detect_language(source)
            backend = get_backend(language)
            result = backend.move_module(source, target, project_root, dry_run, overwrite=overwrite)

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
            line, column = validate_position_selector(
                arguments.get("line"),
                arguments.get("column"),
            )
            language = detect_language(file)
            backend = get_backend(language)
            result = backend.rename_symbol(
                file,
                old_name,
                new_name,
                project_root,
                dry_run,
                line=line,
                column=column,
            )

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

        elif name == "organize_imports":
            backend = get_backend("python")
            result = backend.organize_imports(
                arguments["file"],
                project_root,
                dry_run,
            )

        elif name == "extract_variable":
            backend = get_backend("python")
            result = backend.extract_variable(
                file=arguments["file"],
                new_name=arguments["new_name"],
                start_line=arguments["start_line"],
                start_column=arguments["start_column"],
                end_line=arguments["end_line"],
                end_column=arguments["end_column"],
                project_root=project_root,
                dry_run=dry_run,
            )

        elif name == "extract_function":
            backend = get_backend("python")
            result = backend.extract_function(
                file=arguments["file"],
                new_name=arguments["new_name"],
                start_line=arguments["start_line"],
                start_column=arguments["start_column"],
                end_line=arguments["end_line"],
                end_column=arguments["end_column"],
                project_root=project_root,
                dry_run=dry_run,
            )

        elif name == "inline_symbol":
            backend = get_backend("python")
            result = backend.inline_symbol(
                file=arguments["file"],
                line=arguments["line"],
                column=arguments["column"],
                project_root=project_root,
                dry_run=dry_run,
            )

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
