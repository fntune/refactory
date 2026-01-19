"""Python refactoring backend using Rope."""
import ast
import importlib.util
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("refactory.python")


class PythonBackend:
    """Python refactoring using Rope library."""

    def _validate_path(self, path: str, project_root: str) -> Path:
        """Validate path stays within project root. Raises ValueError if not."""
        root = Path(project_root).resolve()
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Path '{path}' escapes project root")
        return resolved

    def _get_project(self, project_root: str):
        """Get or create Rope project."""
        from rope.base.project import Project
        root = Path(project_root).resolve()
        if not root.exists():
            raise ValueError(f"Project root does not exist: {project_root}")
        return Project(str(root))

    def _find_resource(self, project, file_path: str):
        """Find a resource in the project."""
        return project.get_resource(file_path)

    def _find_symbol_offset(self, source: str, name: str) -> int:
        """Find byte offset of a symbol definition using AST."""
        tree = ast.parse(source)

        for node in ast.walk(tree):
            # Function definitions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    return self._get_name_offset(source, node.lineno, node.col_offset, name)
            # Class definitions
            elif isinstance(node, ast.ClassDef):
                if node.name == name:
                    return self._get_name_offset(source, node.lineno, node.col_offset, name)
            # Variable assignments at module level
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        return self._get_name_offset(source, target.lineno, target.col_offset, name)
            # Annotated assignments
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == name:
                    return self._get_name_offset(source, node.target.lineno, node.target.col_offset, name)

        raise ValueError(f"Symbol '{name}' not found in file")

    def _get_name_offset(self, source: str, lineno: int, col_offset: int, name: str) -> int:
        """Convert line/col to byte offset, finding the actual name position."""
        lines = source.splitlines(keepends=True)
        offset = sum(len(lines[i]) for i in range(lineno - 1))
        # Find the name starting from col_offset (skip 'def ', 'class ', etc.)
        line = lines[lineno - 1] if lineno <= len(lines) else ""
        name_start = line.find(name, col_offset)
        if name_start == -1:
            name_start = col_offset
        return offset + name_start

    def move_module(
        self, source: str, target: str, project_root: str, dry_run: bool
    ) -> dict[str, Any]:
        """Move a Python module and update all imports."""
        from rope.refactor.move import MoveModule
        from rope.refactor.rename import Rename

        # Validate paths
        self._validate_path(source, project_root)
        self._validate_path(target, project_root)

        project = self._get_project(project_root)
        try:
            resource = self._find_resource(project, source)
            target_path = Path(target)
            target_dir = str(target_path.parent)
            source_stem = Path(source).stem
            target_stem = target_path.stem

            # Ensure target directory exists as a package
            target_dir_path = Path(project_root) / target_dir
            if not dry_run:
                target_dir_path.mkdir(parents=True, exist_ok=True)
                init_file = target_dir_path / "__init__.py"
                if not init_file.exists():
                    init_file.touch()
            elif not target_dir_path.exists():
                # For dry_run, create dir temporarily to compute changes
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
                if source_stem != target_stem:
                    # Use Rope's rename to properly update imports
                    moved_file_path = f"{target_dir}/{source_stem}.py"
                    try:
                        moved_resource = self._find_resource(project, moved_file_path)
                        renamer = Rename(project, moved_resource, None)
                        rename_changes = renamer.get_changes(target_stem)
                        project.do(rename_changes)
                        affected_files.extend(
                            f.path for f in rename_changes.get_changed_resources()
                            if f.path not in affected_files
                        )
                    except Exception as e:
                        # Fallback to manual rename if Rope rename fails
                        logger.warning(f"Rope rename failed, using manual rename: {e}")
                        moved_file = Path(project_root) / target_dir / f"{source_stem}.py"
                        final_file = Path(project_root) / target
                        if moved_file.exists():
                            moved_file.rename(final_file)

            return {
                "success": True,
                "dry_run": dry_run,
                "source": source,
                "target": target,
                "affected_files": list(set(affected_files)),
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

        # Validate paths
        self._validate_path(source_file, project_root)
        self._validate_path(target_file, project_root)

        project = self._get_project(project_root)
        try:
            resource = self._find_resource(project, source_file)
            source_code = resource.read()
            offset = self._find_symbol_offset(source_code, symbol_name)

            # Ensure target file exists
            target_path = Path(project_root) / target_file
            if not target_path.exists():
                if not dry_run:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.touch()
                else:
                    raise ValueError(f"Target file does not exist: {target_file}")

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

        # Validate path
        self._validate_path(file, project_root)

        project = self._get_project(project_root)
        try:
            resource = self._find_resource(project, file)
            source_code = resource.read()
            offset = self._find_symbol_offset(source_code, old_name)

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
        errors = []
        root = Path(project_root).resolve()

        if not root.exists():
            return [{"error": f"Project root does not exist: {project_root}", "type": "invalid_root"}]

        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)

                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            module_path = node.module.replace(".", "/")
                            possible_paths = [
                                root / f"{module_path}.py",
                                root / module_path / "__init__.py",
                            ]
                            if not any(p.exists() for p in possible_paths):
                                # Check if it's a stdlib or third-party module
                                # Use find_spec instead of __import__ to avoid code execution
                                top_module = node.module.split(".")[0]
                                if importlib.util.find_spec(top_module) is None:
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
