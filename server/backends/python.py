"""Python refactoring backend using Rope."""
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("refactory.python")


class PythonBackend:
    """Python refactoring using Rope library."""

    def _get_project(self, project_root: str):
        """Get or create Rope project."""
        from rope.base.project import Project
        return Project(project_root)

    def _find_resource(self, project, file_path: str):
        """Find a resource in the project."""
        return project.get_resource(file_path)

    def _find_offset(self, source: str, name: str) -> int:
        """Find byte offset of a name in source code."""
        import re
        pattern = rf"\b{re.escape(name)}\b"
        match = re.search(pattern, source)
        if not match:
            raise ValueError(f"Symbol '{name}' not found in file")
        return match.start()

    def move_module(
        self, source: str, target: str, project_root: str, dry_run: bool
    ) -> dict[str, Any]:
        """Move a Python module and update all imports."""
        from rope.refactor.move import MoveModule

        project = self._get_project(project_root)
        try:
            resource = self._find_resource(project, source)
            target_path = Path(target)
            target_dir = str(target_path.parent)

            # Ensure target directory exists as a package
            target_dir_path = Path(project_root) / target_dir
            target_dir_path.mkdir(parents=True, exist_ok=True)
            init_file = target_dir_path / "__init__.py"
            if not init_file.exists():
                init_file.touch()

            dest_resource = project.get_resource(target_dir)
            mover = MoveModule(project, resource)
            changes = mover.get_changes(dest_resource)

            affected_files = [f.path for f in changes.get_changed_resources()]

            if not dry_run:
                project.do(changes)
                # Handle rename if target filename differs
                if target_path.stem != Path(source).stem:
                    moved_file = Path(project_root) / target_dir / Path(source).name
                    final_file = Path(project_root) / target
                    if moved_file.exists():
                        moved_file.rename(final_file)

            return {
                "success": True,
                "dry_run": dry_run,
                "source": source,
                "target": target,
                "affected_files": affected_files,
                "changes_count": len(affected_files),
            }
        finally:
            project.close()

    def move_symbol(
        self,
        source_file: str,
        symbol_name: str,
        target_file: str,
        project_root: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Move a symbol (function/class) to another module."""
        from rope.refactor.move import MoveGlobal

        project = self._get_project(project_root)
        try:
            resource = self._find_resource(project, source_file)
            source_code = resource.read()
            offset = self._find_offset(source_code, symbol_name)

            # Ensure target file exists
            target_path = Path(project_root) / target_file
            if not target_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.touch()

            dest_resource = self._find_resource(project, target_file)
            mover = MoveGlobal(project, resource, offset)
            changes = mover.get_changes(dest_resource)

            affected_files = [f.path for f in changes.get_changed_resources()]

            if not dry_run:
                project.do(changes)

            return {
                "success": True,
                "dry_run": dry_run,
                "symbol": symbol_name,
                "source": source_file,
                "target": target_file,
                "affected_files": affected_files,
            }
        finally:
            project.close()

    def rename_symbol(
        self, file: str, old_name: str, new_name: str, project_root: str, dry_run: bool
    ) -> dict[str, Any]:
        """Rename a symbol across the codebase."""
        from rope.refactor.rename import Rename

        project = self._get_project(project_root)
        try:
            resource = self._find_resource(project, file)
            source_code = resource.read()
            offset = self._find_offset(source_code, old_name)

            renamer = Rename(project, resource, offset)
            changes = renamer.get_changes(new_name)

            affected_files = [f.path for f in changes.get_changed_resources()]

            if not dry_run:
                project.do(changes)

            return {
                "success": True,
                "dry_run": dry_run,
                "old_name": old_name,
                "new_name": new_name,
                "file": file,
                "affected_files": affected_files,
            }
        finally:
            project.close()

    def validate_imports(self, project_root: str) -> list[dict[str, Any]]:
        """Check for broken imports in Python files."""
        import ast

        errors = []
        root = Path(project_root)

        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)

                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        # Try to resolve the import
                        if isinstance(node, ast.ImportFrom) and node.module:
                            module_path = node.module.replace(".", "/")
                            possible_paths = [
                                root / f"{module_path}.py",
                                root / module_path / "__init__.py",
                            ]
                            if not any(p.exists() for p in possible_paths):
                                # Check if it's a stdlib or third-party module
                                try:
                                    __import__(node.module.split(".")[0])
                                except ImportError:
                                    errors.append({
                                        "file": str(py_file.relative_to(root)),
                                        "line": node.lineno,
                                        "import": node.module,
                                        "type": "unresolved_import",
                                    })
            except SyntaxError as e:
                errors.append({
                    "file": str(py_file.relative_to(root)),
                    "line": e.lineno,
                    "error": str(e),
                    "type": "syntax_error",
                })
            except Exception as e:
                logger.warning(f"Error checking {py_file}: {e}")

        return errors
