"""Python refactoring backend using Rope."""
import ast
import importlib
import importlib.util
import logging
import shutil
import tempfile
from collections.abc import Callable
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

    def _run_in_temp_project(
        self,
        project_root: str,
        operation: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        """Run a dry-run operation against an isolated project copy."""
        root = Path(project_root).resolve()
        if not root.exists():
            raise ValueError(f"Project root does not exist: {project_root}")

        with tempfile.TemporaryDirectory(prefix="refactory-python-dry-run-") as temp_dir:
            temp_root = Path(temp_dir) / root.name
            shutil.copytree(
                root,
                temp_root,
                ignore=shutil.ignore_patterns("__pycache__", ".venv", ".ropeproject"),
            )
            result = operation(str(temp_root))

        result["dry_run"] = True
        return result

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

    def _module_parts_for_file(self, py_file: Path, root: Path) -> list[str]:
        """Get the dotted module path for a file relative to the project root."""
        module_parts = list(py_file.relative_to(root).with_suffix("").parts)
        if module_parts and module_parts[-1] == "__init__":
            module_parts.pop()
        return module_parts

    def _resolve_import_parts(
        self,
        py_file: Path,
        root: Path,
        module: str | None,
        level: int,
    ) -> list[str] | None:
        """Resolve an import target to module parts within the project."""
        package_parts = self._module_parts_for_file(py_file, root)
        if py_file.name != "__init__.py" and package_parts:
            package_parts = package_parts[:-1]

        if level:
            parent_hops = level - 1
            if parent_hops > len(package_parts):
                return None
            base_parts = package_parts[: len(package_parts) - parent_hops]
        else:
            base_parts = []

        module_parts = module.split(".") if module else []
        return base_parts + module_parts

    def _find_local_module(self, root: Path, module_parts: list[str]) -> Path | None:
        """Find a local module file or package init for module parts."""
        if not module_parts:
            init_file = root / "__init__.py"
            return init_file if init_file.exists() else None

        module_base = root.joinpath(*module_parts)
        module_file = module_base.with_suffix(".py")
        if module_file.exists():
            return module_file

        package_init = module_base / "__init__.py"
        if package_init.exists():
            return package_init

        if module_base.is_dir():
            return module_base

        return None

    def _module_exists_externally(self, module_name: str) -> bool:
        """Check whether a module resolves outside the project."""
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def _collect_external_bound_names(
        self,
        module_name: str,
        external_exports_cache: dict[str, set[str] | None],
    ) -> set[str] | None:
        """Collect statically visible names for source-based external modules."""
        cached = external_exports_cache.get(module_name)
        if module_name in external_exports_cache:
            return cached

        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            external_exports_cache[module_name] = None
            return None

        origin = getattr(spec, "origin", None)
        if spec is None:
            external_exports_cache[module_name] = None
            return None

        try:
            module = importlib.import_module(module_name)
        except Exception:
            module = None

        if module is not None:
            names = set(dir(module))
            external_exports_cache[module_name] = names
            return names

        if origin in {None, "built-in", "frozen"}:
            external_exports_cache[module_name] = None
            return None

        origin_path = Path(origin)
        if origin_path.suffix not in {".py", ".pyi"} or not origin_path.exists():
            external_exports_cache[module_name] = None
            return None

        names: set[str] = set()
        try:
            tree = ast.parse(origin_path.read_text())
        except SyntaxError:
            external_exports_cache[module_name] = None
            return None

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue

            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                continue

            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
                continue

            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name)

        external_exports_cache[module_name] = names
        return names

    def _collect_bound_names(
        self,
        module_path: Path,
        root: Path,
        exports_cache: dict[Path, set[str]],
    ) -> set[str]:
        """Collect names bound at module top level without executing code."""
        cached = exports_cache.get(module_path)
        if cached is not None:
            return cached

        names: set[str] = set()
        if module_path.is_dir():
            exports_cache[module_path] = names
            return names

        try:
            tree = ast.parse(module_path.read_text())
        except SyntaxError:
            exports_cache[module_path] = names
            return names

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue

            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                continue

            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_parts = alias.name.split(".")
                    if self._find_local_module(root, module_parts) is not None:
                        names.add(alias.asname or alias.name.split(".")[0])
                        continue
                    if self._module_exists_externally(alias.name):
                        names.add(alias.asname or alias.name.split(".")[0])
                continue

            if isinstance(node, ast.ImportFrom):
                module_parts = self._resolve_import_parts(module_path, root, node.module, node.level)
                if module_parts is None:
                    continue

                local_module = self._find_local_module(root, module_parts)
                if local_module is None:
                    external_name = ".".join(module_parts)
                    if node.level or not external_name or not self._module_exists_externally(external_name):
                        continue

                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name)

        exports_cache[module_path] = names
        return names

    def _format_import_target(self, module: str | None, level: int) -> str:
        """Render an import target for error messages."""
        prefix = "." * level
        if module:
            return f"{prefix}{module}"
        return prefix or "<module>"

    def _validate_import_node(
        self,
        node: ast.Import,
        py_file: Path,
        root: Path,
    ) -> list[dict[str, Any]]:
        """Validate plain import statements."""
        errors = []
        for alias in node.names:
            module_parts = alias.name.split(".")
            if self._find_local_module(root, module_parts) is not None:
                continue
            if self._module_exists_externally(alias.name):
                continue

            errors.append({
                "file": str(py_file.relative_to(root)),
                "line": node.lineno,
                "import": alias.name,
                "type": "unresolved_import",
            })

        return errors

    def _validate_import_from_node(
        self,
        node: ast.ImportFrom,
        py_file: Path,
        root: Path,
        exports_cache: dict[Path, set[str]],
        external_exports_cache: dict[str, set[str] | None],
    ) -> list[dict[str, Any]]:
        """Validate from-import statements without importing user code."""
        errors = []
        import_target = self._format_import_target(node.module, node.level)
        module_parts = self._resolve_import_parts(py_file, root, node.module, node.level)

        if module_parts is None:
            return [{
                "file": str(py_file.relative_to(root)),
                "line": node.lineno,
                "import": import_target,
                "type": "unresolved_import",
            }]

        local_module = self._find_local_module(root, module_parts)
        if local_module is None:
            if node.level:
                return [{
                    "file": str(py_file.relative_to(root)),
                    "line": node.lineno,
                    "import": import_target,
                    "type": "unresolved_import",
                }]

            external_name = ".".join(module_parts)
            if not external_name or not self._module_exists_externally(external_name):
                return [{
                    "file": str(py_file.relative_to(root)),
                    "line": node.lineno,
                    "import": import_target,
                    "type": "unresolved_import",
                }]

        if any(alias.name == "*" for alias in node.names):
            return errors

        if local_module is not None:
            available_names = self._collect_bound_names(local_module, root, exports_cache)
            for alias in node.names:
                imported_name = alias.name
                if imported_name in available_names:
                    continue
                if self._find_local_module(root, module_parts + [imported_name]) is not None:
                    continue

                errors.append({
                    "file": str(py_file.relative_to(root)),
                    "line": node.lineno,
                    "import": import_target,
                    "name": imported_name,
                    "type": "unresolved_import_name",
                })

            return errors

        external_name = ".".join(module_parts)
        external_names = self._collect_external_bound_names(
            external_name,
            external_exports_cache,
        )
        if external_names is None:
            return errors

        for alias in node.names:
            imported_name = alias.name
            if imported_name not in external_names:
                errors.append({
                    "file": str(py_file.relative_to(root)),
                    "line": node.lineno,
                    "import": import_target,
                    "name": imported_name,
                    "type": "unresolved_import_name",
                })

        return errors

    def move_module(
        self, source: str, target: str, project_root: str, dry_run: bool
    ) -> dict[str, Any]:
        """Move a Python module and update all imports."""
        from rope.refactor.move import MoveModule
        from rope.refactor.rename import Rename

        if dry_run:
            return self._run_in_temp_project(
                project_root,
                lambda temp_root: self.move_module(source, target, temp_root, False),
            )

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
            if not target_dir_path.exists():
                target_dir_path.mkdir(parents=True, exist_ok=True)
                init_file = target_dir_path / "__init__.py"
                if not init_file.exists():
                    init_file.touch()

            dest_resource = project.get_resource(target_dir)
            mover = MoveModule(project, resource)
            changes = mover.get_changes(dest_resource)

            affected_files = [f.path for f in changes.get_changed_resources()]

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

        if dry_run:
            return self._run_in_temp_project(
                project_root,
                lambda temp_root: self.move_symbol(
                    source_file,
                    symbol_name,
                    target_file,
                    temp_root,
                    False,
                ),
            )

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

        if dry_run:
            return self._run_in_temp_project(
                project_root,
                lambda temp_root: self.rename_symbol(
                    file,
                    old_name,
                    new_name,
                    temp_root,
                    False,
                ),
            )

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
        exports_cache: dict[Path, set[str]] = {}
        external_exports_cache: dict[str, set[str] | None] = {}

        if not root.exists():
            return [{"error": f"Project root does not exist: {project_root}", "type": "invalid_root"}]

        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file) or ".venv" in str(py_file):
                continue
            try:
                source = py_file.read_text()
                tree = ast.parse(source)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        errors.extend(self._validate_import_node(node, py_file, root))
                    elif isinstance(node, ast.ImportFrom):
                        errors.extend(
                            self._validate_import_from_node(
                                node,
                                py_file,
                                root,
                                exports_cache,
                                external_exports_cache,
                            )
                        )
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
