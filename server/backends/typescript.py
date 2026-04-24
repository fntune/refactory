"""TypeScript refactoring backend using ts-morph via subprocess."""
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from validation import validate_identifier, validate_position_selector

logger = logging.getLogger("refactory.typescript")

TSMORPH_SCRIPT = Path(__file__).parent.parent / "tsmorph" / "refactor.js"


class TypeScriptBackend:
    """TypeScript refactoring using ts-morph library."""

    def _run_tsmorph(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run ts-morph refactoring script."""
        if not TSMORPH_SCRIPT.exists():
            raise RuntimeError(
                f"ts-morph script not found at {TSMORPH_SCRIPT}. "
                "Run the install-deps hook or install manually."
            )

        cmd = [
            "node",
            str(TSMORPH_SCRIPT),
            operation,
            json.dumps(args),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise RuntimeError(f"ts-morph failed: {result.stderr}")

        return json.loads(result.stdout)

    def move_module(
        self,
        source: str,
        target: str,
        project_root: str,
        dry_run: bool,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Move a TypeScript module and update all imports."""
        return self._run_tsmorph("move_module", {
            "source": source,
            "target": target,
            "projectRoot": project_root,
            "dryRun": dry_run,
            "overwrite": overwrite,
        })

    def move_symbol(
        self,
        source_file: str,
        symbol_name: str,
        target_file: str,
        project_root: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Move a symbol (function/class) to another module."""
        validate_identifier(symbol_name, "typescript")
        return self._run_tsmorph("move_symbol", {
            "sourceFile": source_file,
            "symbolName": symbol_name,
            "targetFile": target_file,
            "projectRoot": project_root,
            "dryRun": dry_run,
        })

    def rename_symbol(
        self,
        file: str,
        old_name: str,
        new_name: str,
        project_root: str,
        dry_run: bool,
        line: int | None = None,
        column: int | None = None,
    ) -> dict[str, Any]:
        """Rename a symbol across the codebase."""
        validate_identifier(new_name, "typescript")
        line, column = validate_position_selector(line, column)
        return self._run_tsmorph("rename_symbol", {
            "file": file,
            "oldName": old_name,
            "newName": new_name,
            "projectRoot": project_root,
            "dryRun": dry_run,
            "line": line,
            "column": column,
        })

    def validate_imports(self, project_root: str) -> list[dict[str, Any]]:
        """Check for broken imports in TypeScript files."""
        result = self._run_tsmorph("validate_imports", {
            "projectRoot": project_root,
        })
        return result.get("errors", [])
