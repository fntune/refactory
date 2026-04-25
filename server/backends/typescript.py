"""TypeScript refactoring backend using ts-morph via subprocess."""
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from validation import validate_identifier, validate_position_selector

logger = logging.getLogger("refactory.typescript")

TSMORPH_DIR = Path(__file__).parent.parent / "tsmorph"
TSMORPH_SCRIPT = TSMORPH_DIR / "refactor.js"
TSMORPH_MODULE_MARKER = TSMORPH_DIR / "node_modules" / "ts-morph" / "package.json"


class TypeScriptBackend:
    """TypeScript refactoring using ts-morph library."""

    def __init__(self) -> None:
        self._dependency_error: str | None = self._check_dependencies()

    @staticmethod
    def _check_dependencies() -> str | None:
        """Return an actionable error string when ts-morph is unavailable, else None.

        Cached on the backend instance so we do not stat the filesystem on every
        tool call. If ts-morph is installed but broken at runtime, the subprocess
        will still surface the underlying node error — this probe only catches
        the common case of "the install hook never ran."
        """
        if not TSMORPH_SCRIPT.exists():
            return (
                f"ts-morph script not found at {TSMORPH_SCRIPT}. "
                f"Reinstall the refactory plugin or run: "
                f"cd {TSMORPH_DIR} && pnpm install"
            )
        if not TSMORPH_MODULE_MARKER.exists():
            return (
                f"ts-morph is not installed. "
                f"Run: cd {TSMORPH_DIR} && pnpm install "
                f"(or: npm install). The SessionStart hook normally handles this "
                f"automatically; run it manually if the hook did not fire."
            )
        return None

    def _run_tsmorph(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run ts-morph refactoring script."""
        if self._dependency_error is not None:
            raise RuntimeError(self._dependency_error)

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

    def _git_worktree_root(self, path: Path) -> Path | None:
        """Return the git worktree root containing path, or None outside git."""
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return Path(result.stdout.strip()).resolve()

    def _prepare_project_root(
        self,
        project_root: str,
        expected_git_root: str | None,
    ) -> str:
        """Resolve project_root and verify it belongs to the expected worktree."""
        root = Path(project_root).expanduser().resolve()
        if expected_git_root is None:
            return str(root)

        expected = Path(expected_git_root).expanduser().resolve()
        if not expected.exists():
            raise ValueError(f"expected_git_root does not exist: {expected_git_root}")

        git_root = self._git_worktree_root(root)
        if git_root is None:
            raise ValueError(
                f"project_root '{root}' is not inside a git worktree, "
                f"but expected_git_root was set to '{expected}'"
            )
        if git_root != expected:
            raise ValueError(
                f"project_root '{root}' belongs to git worktree '{git_root}', "
                f"not expected_git_root '{expected}'"
            )
        if not root.is_relative_to(expected):
            raise ValueError(
                f"project_root '{root}' is not inside expected_git_root '{expected}'"
            )
        return str(root)

    def move_module(
        self,
        source: str,
        target: str,
        project_root: str,
        dry_run: bool,
        overwrite: bool = False,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Move a TypeScript module and update all imports."""
        project_root = self._prepare_project_root(project_root, expected_git_root)
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
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Move a symbol (function/class) to another module."""
        validate_identifier(symbol_name, "typescript")
        project_root = self._prepare_project_root(project_root, expected_git_root)
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
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Rename a symbol across the codebase."""
        validate_identifier(new_name, "typescript")
        line, column = validate_position_selector(line, column)
        project_root = self._prepare_project_root(project_root, expected_git_root)
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
