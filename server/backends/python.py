"""Python refactoring backend using Rope."""
import ast
import importlib.machinery
import importlib.util
import logging
import subprocess
from pathlib import Path
from typing import Any

from rope.base.change import CreateFile, CreateFolder, RemoveResource
from rope.refactor.extract import ExtractMethod, ExtractVariable
from rope.refactor.importutils import ImportOrganizer
from rope.refactor.inline import create_inline
from rope.refactor.move import MoveGlobal, MoveModule
from rope.refactor.rename import Rename

from validation import validate_identifier, validate_position_selector

logger = logging.getLogger("refactory.python")

SCAN_SKIP_DIR_NAMES = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ropeproject",
    "node_modules",
    "site-packages",
    ".eggs",
})


class PythonBackend:
    """Python refactoring using Rope library."""

    def _validate_path(self, path: str, project_root: str) -> Path:
        """Validate path stays within project root."""
        root = Path(project_root).resolve()
        if not root.exists():
            raise ValueError(f"Project root does not exist: {project_root}")
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Path '{path}' escapes project root")
        return resolved

    def _validate_source_path(self, path: str, project_root: str) -> Path:
        """Validate path stays within project root and refers to an existing file."""
        resolved = self._validate_path(path, project_root)
        if not resolved.exists():
            raise ValueError(f"source file not found: {path}")
        if not resolved.is_file():
            raise ValueError(f"source path is not a file: {path}")
        return resolved

    def _validate_module_path(self, file_path: str) -> None:
        """Validate Python package and module segments in a file path."""
        target = Path(file_path)
        if target.suffix != ".py":
            raise ValueError(f"Target path must be a Python file: {file_path}")

        segments = [*target.parts[:-1], target.stem]
        for segment in segments:
            if segment in {"", "."}:
                continue
            if not segment.isidentifier():
                raise ValueError(f"target path contains invalid Python module name: '{segment}'")

    def _get_project(self, project_root: str):
        """Open and validate a Rope project."""
        from rope.base.project import Project

        root = Path(project_root).resolve()
        if not root.exists():
            raise ValueError(f"Project root does not exist: {project_root}")
        project = Project(str(root))
        project.validate(project.root)
        return project

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
    ) -> tuple[Path, Path | None]:
        """Resolve project_root and verify it belongs to the expected worktree."""
        root = Path(project_root).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"Project root does not exist: {project_root}")

        git_root = self._git_worktree_root(root)
        if expected_git_root is None:
            return root, git_root

        expected = Path(expected_git_root).expanduser().resolve()
        if not expected.exists():
            raise ValueError(f"expected_git_root does not exist: {expected_git_root}")
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
        return root, git_root

    def _relative_path(self, path: Path, root: Path) -> str:
        return path.resolve().relative_to(root).as_posix()

    def _get_name_offset(self, source: str, lineno: int, col_offset: int, name: str) -> int:
        """Convert line/column into a character offset."""
        lines = source.splitlines(keepends=True)
        offset = sum(len(lines[index]) for index in range(lineno - 1))
        line = lines[lineno - 1] if lineno <= len(lines) else ""
        name_start = line.find(name, col_offset)
        if name_start == -1:
            name_start = col_offset
        return offset + name_start

    def _position_to_offset(self, source: str, line: int, column: int) -> int:
        """Convert a 1-based line/column position to a character offset."""
        lines = source.splitlines(keepends=True)
        if line < 1 or line > len(lines):
            raise ValueError("line is out of range")
        line_text = lines[line - 1]
        visible = line_text.rstrip("\r\n")
        max_column = len(visible) + 1
        if column < 1 or column > max_column:
            raise ValueError("column is out of range")
        return sum(len(lines[index]) for index in range(line - 1)) + column - 1

    def _range_to_offsets(
        self,
        source: str,
        start_line: int,
        start_column: int,
        end_line: int,
        end_column: int,
    ) -> tuple[int, int]:
        """Convert an inclusive start/exclusive end range into offsets."""
        start = self._position_to_offset(source, start_line, start_column)
        end = self._position_to_offset(source, end_line, end_column)
        if end <= start:
            raise ValueError("selection end must be after selection start")
        return start, end

    def _candidate_matches_selector(
        self,
        candidate: dict[str, Any],
        line: int | None,
        column: int | None,
    ) -> bool:
        """Check whether a candidate matches the optional selector."""
        if line is None:
            return True
        if candidate["line"] != line:
            return False
        if column is None:
            return True
        return candidate["column"] == column

    def _iter_named_candidates(self, tree: ast.AST, source: str, name: str) -> list[dict[str, Any]]:
        """Collect declaration candidates for a symbol name.

        Walks the entire tree so methods, nested functions, local variables,
        and parameters all become candidates. When multiple candidates share
        a name, ``_resolve_named_candidate`` raises unless the caller passes
        line/column to disambiguate — safer than silently renaming the
        wrong symbol.
        """
        candidates: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                candidates.append({
                    "kind": "function",
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "offset": self._get_name_offset(source, node.lineno, node.col_offset, name),
                    "parameter": False,
                })
                continue

            if isinstance(node, ast.ClassDef) and node.name == name:
                candidates.append({
                    "kind": "class",
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "offset": self._get_name_offset(source, node.lineno, node.col_offset, name),
                    "parameter": False,
                })
                continue

            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        candidates.append({
                            "kind": "variable",
                            "line": target.lineno,
                            "column": target.col_offset + 1,
                            "offset": self._get_name_offset(source, target.lineno, target.col_offset, name),
                            "parameter": False,
                        })
                continue

            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                candidates.append({
                    "kind": "variable",
                    "line": node.target.lineno,
                    "column": node.target.col_offset + 1,
                    "offset": self._get_name_offset(source, node.target.lineno, node.target.col_offset, name),
                    "parameter": False,
                })
                continue

            if isinstance(node, ast.arg) and node.arg == name:
                candidates.append({
                    "kind": "parameter",
                    "line": node.lineno,
                    "column": node.col_offset + 1,
                    "offset": self._get_name_offset(source, node.lineno, node.col_offset, name),
                    "parameter": True,
                })

        return candidates

    def _resolve_named_candidate(
        self,
        source: str,
        name: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> dict[str, Any]:
        """Resolve a declaration candidate, honoring selectors when present."""
        tree = ast.parse(source)
        candidates = self._iter_named_candidates(tree, source, name)
        matching = [
            candidate
            for candidate in candidates
            if self._candidate_matches_selector(candidate, line, column)
        ]

        if not matching:
            raise ValueError(f"Symbol '{name}' not found in file")

        if line is not None:
            if len(matching) == 1:
                return matching[0]
            raise ValueError(f"Symbol '{name}' is ambiguous at line {line}; pass column")

        if len(matching) == 1:
            return matching[0]

        raise ValueError(f"Symbol '{name}' is ambiguous in file; pass line and column")

    def _contains_name(self, target: ast.AST, name: str) -> bool:
        """Check whether a target subtree contains a name."""
        return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(target))

    def _find_move_symbol_offset(self, source: str, name: str) -> int:
        """Resolve a moveable top-level declaration offset."""
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return self._get_name_offset(source, node.lineno, node.col_offset, name)

            if isinstance(node, ast.ClassDef) and node.name == name:
                return self._get_name_offset(source, node.lineno, node.col_offset, name)

            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    if any(self._contains_name(target, name) for target in node.targets):
                        raise ValueError(
                            f"cannot move '{name}' from a multi-target or destructuring assignment; split it first"
                        )
                    continue

                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    if self._contains_name(target, name):
                        raise ValueError(
                            f"cannot move '{name}' from a multi-target or destructuring assignment; split it first"
                        )
                    continue

                if target.id == name:
                    return self._get_name_offset(source, target.lineno, target.col_offset, name)

            if isinstance(node, ast.AnnAssign):
                target = node.target
                if not isinstance(target, ast.Name):
                    if self._contains_name(target, name):
                        raise ValueError(
                            f"cannot move '{name}' from a multi-target or destructuring assignment; split it first"
                        )
                    continue
                if target.id == name:
                    return self._get_name_offset(source, target.lineno, target.col_offset, name)

        raise ValueError(f"Symbol '{name}' not found in file")

    def _resource_for_existing_path(self, project, path: Path):
        return project.get_resource(self._relative_path(path, Path(project.address).resolve()))

    def _stage_package_structure(
        self,
        project,
        root: Path,
        folder_path: Path,
        apply_staging: bool,
    ) -> tuple[Any, list[str], list[str], int]:
        """Ensure a folder path exists as a Python package using Rope changes."""
        if folder_path == root:
            return project.root, [], [], 0

        current = project.root
        current_rel = Path()
        created_init_paths: list[str] = []
        preview_parts: list[str] = []
        applied_count = 0

        for part in folder_path.relative_to(root).parts:
            child_rel = (current_rel / part).as_posix()
            child_path = root / child_rel
            if child_path.exists():
                child = project.get_resource(child_rel)
                if not child.is_folder():
                    raise ValueError(f"target path collides with existing file: {child_rel}")
            else:
                change = CreateFolder(current, part)
                preview_parts.append(change.get_description())
                if apply_staging:
                    self._do(project, root, change)
                    applied_count += 1
                    child = project.get_resource(child_rel)
                else:
                    child = project.get_folder(child_rel)

            init_rel = f"{child_rel}/__init__.py"
            init_path = root / init_rel
            if init_path.exists():
                init_resource = project.get_resource(init_rel)
                if init_resource.is_folder():
                    raise ValueError(f"target path collides with existing directory: {init_rel}")
            else:
                change = CreateFile(child, "__init__.py")
                preview_parts.append(change.get_description())
                if apply_staging:
                    self._do(project, root, change)
                    applied_count += 1
                created_init_paths.append(init_rel)

            current = child
            current_rel = Path(child_rel)

        return current, created_init_paths, preview_parts, applied_count

    def _stage_file_resource(
        self,
        project,
        root: Path,
        file_path: Path,
        apply_staging: bool,
    ) -> tuple[Any, list[str], list[str], int]:
        """Ensure a file resource exists using Rope changes."""
        folder_resource, created_inits, preview_parts, applied_count = self._stage_package_structure(
            project,
            root,
            file_path.parent,
            apply_staging,
        )
        rel_path = self._relative_path(file_path, root)
        if file_path.exists():
            resource = project.get_resource(rel_path)
            if resource.is_folder():
                raise ValueError(f"target path collides with existing directory: {rel_path}")
        else:
            change = CreateFile(folder_resource, file_path.name)
            preview_parts.append(change.get_description())
            if apply_staging:
                self._do(project, root, change)
                applied_count += 1
                resource = project.get_resource(rel_path)
            else:
                resource = project.get_file(rel_path)
        return resource, created_inits, preview_parts, applied_count

    def _stage_remove_existing_target(
        self,
        project,
        root: Path,
        target_path: Path,
        apply_staging: bool,
    ) -> tuple[list[str], int]:
        """Remove an existing target resource using Rope changes."""
        rel_path = self._relative_path(target_path, root)
        try:
            resource = project.get_resource(rel_path)
        except Exception:
            return [], 0
        change = RemoveResource(resource)
        if apply_staging:
            self._do(project, root, change)
            return [change.get_description()], 1
        return [change.get_description()], 0

    def _resource_path(self, project, resource: Any) -> Path:
        path = getattr(resource, "path", "")
        project_root = Path(project.address).resolve()
        if not path:
            return project_root
        return (project_root / path).resolve()

    def _assert_change_inside_project_root(self, project, root: Path, change: Any) -> None:
        """Refuse to apply a Rope Change that targets files outside project_root."""
        outside: list[str] = []
        for resource in change.get_changed_resources():
            resource_path = self._resource_path(project, resource)
            if not resource_path.is_relative_to(root):
                outside.append(str(resource_path))
        if outside:
            formatted = "\n".join(f"  {path}" for path in outside)
            raise ValueError(
                f"Refusing to apply Rope change outside project_root '{root}':\n"
                f"{formatted}"
            )

    def _do(self, project, root: Path, change: Any) -> None:
        self._assert_change_inside_project_root(project, root, change)
        project.do(change)

    def _collect_changed_python_files(self, *changes: Any) -> list[str]:
        """Collect changed Python files from Rope Change objects."""
        keep: list[str] = []
        for change in changes:
            if change is None:
                continue
            for resource in change.get_changed_resources():
                path = getattr(resource, "path", "")
                if path.endswith(".py") and path not in keep:
                    keep.append(path)
        return keep

    def _merge_paths(self, *path_groups: list[str] | tuple[str, ...]) -> list[str]:
        keep: list[str] = []
        for group in path_groups:
            for path in group:
                if path and path not in keep:
                    keep.append(path)
        return keep

    def _preview_text(self, *parts: str) -> str:
        return "\n\n".join(part for part in parts if part)

    def _undo(self, project, applied_count: int) -> None:
        for _ in range(applied_count):
            project.history.undo()

    def _move_module_changes(
        self,
        project,
        root: Path,
        source: str,
        target: str,
        overwrite: bool,
        apply_staging: bool,
    ) -> tuple[Any, Any | None, list[str], list[str], int]:
        """Prepare Rope changes for moving a module."""
        source_path = self._validate_source_path(source, str(root))
        target_path = self._validate_path(target, str(root))
        self._validate_module_path(target)
        if source_path == target_path:
            raise ValueError("source and target are identical")
        if target_path.exists() and target_path != source_path and not overwrite:
            raise ValueError(f"target already exists: {target}")

        applied_count = 0
        preview_parts: list[str] = []
        created_init_paths: list[str] = []

        target_folder_resource, created_init_paths, scaffold_preview, scaffold_applied = self._stage_package_structure(
            project,
            root,
            target_path.parent,
            apply_staging,
        )
        preview_parts.extend(scaffold_preview)
        applied_count += scaffold_applied

        if overwrite and target_path.exists() and target_path != source_path:
            remove_preview, remove_applied = self._stage_remove_existing_target(
                project,
                root,
                target_path,
                apply_staging,
            )
            preview_parts.extend(remove_preview)
            applied_count += remove_applied

        source_resource = self._resource_for_existing_path(project, source_path)
        mover = MoveModule(project, source_resource)
        move_changes = mover.get_changes(target_folder_resource)
        preview_parts.append(move_changes.get_description())

        rename_changes = None
        if source_path.stem != target_path.stem:
            if apply_staging:
                self._do(project, root, move_changes)
                applied_count += 1
                moved_rel = self._relative_path(target_path.parent / f"{source_path.stem}.py", root)
                moved_resource = project.get_resource(moved_rel)
                renamer = Rename(project, moved_resource, None)
                rename_changes = renamer.get_changes(target_path.stem)
                preview_parts.append(rename_changes.get_description())
            else:
                preview_parts.append(
                    f"Rename moved module from {source_path.stem}.py to {target_path.name}"
                )

        return move_changes, rename_changes, created_init_paths, preview_parts, applied_count

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
            if not package_parts or parent_hops >= len(package_parts):
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

    def _module_spec(self, module_name: str):
        """Resolve an external module spec without importing the module."""
        try:
            return importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            return None

    def _module_exists_externally(self, module_name: str) -> bool:
        """Check whether a module resolves outside the project."""
        return self._module_spec(module_name) is not None

    def _should_skip_scan_path(self, py_file: Path, root: Path) -> bool:
        """True if py_file should be excluded from project-wide scans."""
        try:
            rel = py_file.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            return True
        for part in rel.parts[:-1]:
            if part in SCAN_SKIP_DIR_NAMES:
                return True
            if part.endswith(".egg-info"):
                return True
        return False

    def _iter_project_python_files(self, root: Path):
        """Yield project .py files honoring the standard skip set."""
        for py_file in root.rglob("*.py"):
            if self._should_skip_scan_path(py_file, root):
                continue
            yield py_file

    def _git_tracked_python_files(self, root: Path) -> list[Path] | None:
        """Return tracked and untracked-but-unignored .py files, or None.

        Uses ``git ls-files`` so the result respects ``.gitignore`` and naturally
        skips vendored / build / worktree contents. Submodules are not recursed
        (they are treated as external code). Returns ``None`` when the directory
        is not a git working tree or when ``git`` is unavailable.
        """
        git_root = self._git_worktree_root(root)
        if git_root is None:
            return None
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "*.py",
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        stdout = result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        entries = [entry for entry in stdout.split("\x00") if entry]
        paths = []
        for entry in entries:
            path = (git_root / entry).resolve()
            if path.is_relative_to(root):
                paths.append(path)
        return paths

    def _project_python_files_for_scan(self, root: Path):
        """Yield project .py files, preferring git-tracked files when available.

        Inside a git repo we use ``git ls-files`` so ``.gitignore`` rules,
        ``.git/worktrees/``, and vendored trees are all honored automatically.
        Outside a git repo we fall back to the rglob walk with the standard
        skip set. The skip set is applied in both modes as defense in depth.
        """
        git_files = self._git_tracked_python_files(root)
        if git_files is not None:
            for py_file in git_files:
                if self._should_skip_scan_path(py_file, root):
                    continue
                if not py_file.exists():
                    continue
                yield py_file
            return
        yield from self._iter_project_python_files(root)

    def _same_file(self, a: Path, b: Path) -> bool:
        try:
            return a.resolve() == b.resolve()
        except OSError:
            return False

    def _format_import_from(self, node: ast.ImportFrom) -> str:
        dots = "." * node.level
        module = node.module or ""
        names = ", ".join(
            f"{alias.name} as {alias.asname}" if alias.asname else alias.name
            for alias in node.names
        )
        return f"from {dots}{module} import {names}"

    def _find_lazy_imports_of(
        self,
        py_file: Path,
        root: Path,
        target_source_path: Path,
        *,
        symbol_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return in-function imports in py_file whose target resolves to target_source_path.

        When ``symbol_name`` is provided the filter is narrowed to imports that
        specifically reference that symbol (e.g. ``from source import symbol``).
        Unrelated lazy imports of the same source module are ignored, because
        Rope's ``move_symbol`` only rewrites the moved name — other symbols
        from the module continue to resolve to their original location.
        """
        try:
            tree = ast.parse(py_file.read_text(errors="replace"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            return []

        hits: list[dict[str, Any]] = []

        def visit(node: ast.AST, inside_function: bool) -> None:
            is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))

            if inside_function:
                if isinstance(node, ast.Import):
                    if symbol_name is None:
                        for alias in node.names:
                            resolved = self._find_local_module(root, alias.name.split("."))
                            if resolved and self._same_file(resolved, target_source_path):
                                as_clause = f" as {alias.asname}" if alias.asname else ""
                                hits.append({
                                    "line": node.lineno,
                                    "text": f"import {alias.name}{as_clause}",
                                })
                elif isinstance(node, ast.ImportFrom):
                    module_parts = self._resolve_import_parts(py_file, root, node.module, node.level)
                    if module_parts is not None:
                        matched = False
                        module_file = self._find_local_module(root, module_parts)
                        module_is_target = (
                            module_file is not None
                            and self._same_file(module_file, target_source_path)
                        )
                        if module_is_target:
                            if symbol_name is None:
                                hits.append({
                                    "line": node.lineno,
                                    "text": self._format_import_from(node),
                                })
                                matched = True
                            elif any(alias.name == symbol_name for alias in node.names):
                                hits.append({
                                    "line": node.lineno,
                                    "text": self._format_import_from(node),
                                })
                                matched = True
                        if symbol_name is None and not matched:
                            for alias in node.names:
                                if alias.name == "*":
                                    continue
                                sub_file = self._find_local_module(
                                    root, module_parts + [alias.name]
                                )
                                if sub_file and self._same_file(sub_file, target_source_path):
                                    hits.append({
                                        "line": node.lineno,
                                        "text": self._format_import_from(node),
                                    })
                                    break

            for child in ast.iter_child_nodes(node):
                visit(child, inside_function or is_function)

        visit(tree, False)
        return hits

    def _find_basename_collision(self, source_path: Path) -> dict[str, Any] | None:
        """Return info about a top-level binding named same as source stem, or None."""
        stem = source_path.stem
        try:
            tree = ast.parse(source_path.read_text(errors="replace"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            return None

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == stem:
                return {"line": node.lineno, "binding": stem, "kind": "function"}
            if isinstance(node, ast.ClassDef) and node.name == stem:
                return {"line": node.lineno, "binding": stem, "kind": "class"}
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == stem:
                        return {"line": node.lineno, "binding": stem, "kind": "variable"}
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.target.id == stem:
                return {"line": node.lineno, "binding": stem, "kind": "variable"}
        return None

    def _check_rope_hazards(
        self,
        source_path: Path,
        root: Path,
        operation: str,
        *,
        symbol_name: str | None = None,
    ) -> None:
        """Fail closed when the source file pattern would cause Rope to corrupt output.

        Two known Rope hazards trigger here:
        - E1: in-function (lazy) imports referencing the source module (or, for
              move_symbol, the specific symbol) get silently hoisted to module
              top by Rope, breaking any circular-import workarounds.
        - E2: a top-level binding whose name equals the source file's stem causes
              Rope to confuse variable attribute access (``foo.method()``) with
              module attribute access when rewriting consumers.

        Both hazards are Rope internals; the correct behavior is to refuse the move
        with an actionable message rather than proceed and silently corrupt consumers.
        """
        rel_source = source_path.resolve().relative_to(root.resolve()).as_posix()

        collision = self._find_basename_collision(source_path)
        if collision is not None:
            raise ValueError(
                f"{operation} cannot safely move '{rel_source}' — it exports a "
                f"top-level {collision['kind']} '{collision['binding']}' "
                f"(line {collision['line']}) with the same name as the module. "
                f"The refactor would corrupt "
                f"'{collision['binding']}.method()' calls in consumers. Rename the "
                f"{collision['kind']} first, then retry."
            )

        lazy_hits: list[dict[str, Any]] = []
        for py_file in self._iter_project_python_files(root):
            if self._same_file(py_file, source_path):
                continue
            for hit in self._find_lazy_imports_of(
                py_file, root, source_path, symbol_name=symbol_name
            ):
                lazy_hits.append({
                    "file": py_file.resolve().relative_to(root.resolve()).as_posix(),
                    **hit,
                })

        if lazy_hits:
            sample = lazy_hits[:20]
            formatted = "\n".join(
                f"  {hit['file']}:{hit['line']} — {hit['text']}" for hit in sample
            )
            if len(lazy_hits) > len(sample):
                formatted += f"\n  ...and {len(lazy_hits) - len(sample)} more"
            target_desc = (
                f"'{symbol_name}'" if symbol_name else f"'{source_path.stem}'"
            )
            raise ValueError(
                f"{operation} cannot safely rewrite {target_desc} — "
                f"{len(lazy_hits)} in-function (lazy) import(s) would be hoisted "
                f"to module top by Rope, breaking any circular-import workarounds:\n"
                f"{formatted}\n"
                f"Move these lazy imports to module top (resolving the circular "
                f"dependencies they were working around), then retry."
            )

    def _external_submodule_exists(self, module_name: str, submodule_name: str) -> bool:
        """Check whether an external package exposes a submodule."""
        parent_spec = self._module_spec(module_name)
        search_locations = (
            getattr(parent_spec, "submodule_search_locations", None)
            if parent_spec is not None
            else None
        )
        if not search_locations:
            return False
        try:
            return (
                importlib.machinery.PathFinder.find_spec(
                    submodule_name, list(search_locations),
                )
                is not None
            )
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def _collect_external_bound_names(
        self,
        module_name: str,
        external_exports_cache: dict[str, set[str] | None],
    ) -> set[str] | None:
        """Collect statically visible names for source-based external modules."""
        if module_name in external_exports_cache:
            return external_exports_cache[module_name]

        spec = self._module_spec(module_name)
        if spec is None:
            external_exports_cache[module_name] = None
            return None

        origin = getattr(spec, "origin", None)
        if origin in {None, "built-in", "frozen"}:
            external_exports_cache[module_name] = None
            return None

        origin_path = Path(origin)
        if origin_path.suffix not in {".py", ".pyi"} or not origin_path.exists():
            external_exports_cache[module_name] = None
            return None

        try:
            tree = ast.parse(origin_path.read_text())
        except SyntaxError:
            external_exports_cache[module_name] = None
            return None

        names: set[str] = set()
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
                if any(alias.name == "*" for alias in node.names):
                    external_exports_cache[module_name] = None
                    return None
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name)

        external_exports_cache[module_name] = names
        return names

    def _collect_bound_names(
        self,
        module_path: Path,
        root: Path,
        exports_cache: dict[Path, set[str] | None],
    ) -> set[str] | None:
        """Collect names bound at module top level without executing code."""
        if module_path in exports_cache:
            return exports_cache[module_path]

        names: set[str] = set()
        if module_path.is_dir():
            exports_cache[module_path] = None
            return None

        try:
            tree = ast.parse(module_path.read_text())
        except SyntaxError:
            exports_cache[module_path] = None
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
                    module_parts = alias.name.split(".")
                    if self._find_local_module(root, module_parts) is not None:
                        names.add(alias.asname or alias.name.split(".")[0])
                        continue
                    if self._module_exists_externally(alias.name):
                        names.add(alias.asname or alias.name.split(".")[0])
                continue

            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    exports_cache[module_path] = None
                    return None
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
        exports_cache: dict[Path, set[str] | None],
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
        external_name = ".".join(module_parts)
        if local_module is None:
            if node.level:
                return [{
                    "file": str(py_file.relative_to(root)),
                    "line": node.lineno,
                    "import": import_target,
                    "type": "unresolved_import",
                }]

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
                if available_names is not None and imported_name in available_names:
                    continue
                if self._find_local_module(root, module_parts + [imported_name]) is not None:
                    continue
                if available_names is None:
                    continue

                errors.append({
                    "file": str(py_file.relative_to(root)),
                    "line": node.lineno,
                    "import": import_target,
                    "name": imported_name,
                    "type": "unresolved_import_name",
                })
            return errors

        available_names = self._collect_external_bound_names(external_name, external_exports_cache)
        for alias in node.names:
            imported_name = alias.name
            if available_names is None:
                if self._external_submodule_exists(external_name, imported_name):
                    continue
                continue
            if imported_name in available_names:
                continue
            if self._external_submodule_exists(external_name, imported_name):
                continue

            errors.append({
                "file": str(py_file.relative_to(root)),
                "line": node.lineno,
                "import": import_target,
                "name": imported_name,
                "type": "unresolved_import_name",
            })

        return errors

    def move_module(
        self,
        source: str,
        target: str,
        project_root: str,
        dry_run: bool,
        overwrite: bool = False,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Move a Python module and update all imports."""
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        source_path = self._validate_source_path(source, str(root))
        self._check_rope_hazards(source_path, root, "move_module")
        project = self._get_project(str(root))
        applied_count = 0
        try:
            move_changes, rename_changes, created_init_paths, preview_parts, applied_count = self._move_module_changes(
                project,
                root,
                source,
                target,
                overwrite,
                not dry_run,
            )
            affected_files = self._merge_paths(
                self._collect_changed_python_files(move_changes, rename_changes),
                [source, target],
                created_init_paths,
            )
            result = {
                "success": True,
                "dry_run": dry_run,
                "source": source,
                "target": target,
                "affected_files": affected_files,
                "changes_count": len(affected_files),
            }
            if dry_run:
                result["preview"] = self._preview_text(*preview_parts)
                return result

            if rename_changes is None:
                self._do(project, root, move_changes)
                applied_count += 1
            if rename_changes is not None:
                self._do(project, root, rename_changes)
                applied_count += 1
            return result
        except Exception:
            if not dry_run and applied_count:
                self._undo(project, applied_count)
            raise
        finally:
            if dry_run and applied_count:
                self._undo(project, applied_count)
            project.close()

    def move_symbol(
        self,
        source_file: str,
        symbol_name: str,
        target_file: str,
        project_root: str,
        dry_run: bool,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Move a symbol (function/class/variable) to another module."""
        validate_identifier(symbol_name, "python")
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        source_path = self._validate_source_path(source_file, str(root))
        target_path = self._validate_path(target_file, str(root))
        target_exists = target_path.exists()
        self._validate_module_path(target_file)
        if source_path == target_path:
            raise ValueError("source and target are identical")

        if dry_run and not target_exists:
            raise ValueError(
                f"move_symbol dry-run requires the target module to exist for an exact "
                f"preview; create '{target_file}' first or call the MCP tool with apply: true"
            )

        self._check_rope_hazards(
            source_path,
            root,
            "move_symbol",
            symbol_name=symbol_name,
        )

        project = self._get_project(str(root))
        applied_count = 0
        try:
            target_resource, created_init_paths, preview_parts, applied_count = self._stage_file_resource(
                project,
                root,
                target_path,
                not dry_run,
            )
            source_resource = self._resource_for_existing_path(project, source_path)
            source_code = source_resource.read()
            offset = self._find_move_symbol_offset(source_code, symbol_name)

            mover = MoveGlobal(project, source_resource, offset)
            changes = mover.get_changes(target_resource)
            affected_files = self._merge_paths(
                self._collect_changed_python_files(changes),
                [source_file, target_file],
                created_init_paths,
            )
            result = {
                "success": True,
                "dry_run": dry_run,
                "symbol": symbol_name,
                "source": source_file,
                "target": target_file,
                "affected_files": affected_files,
            }
            if dry_run:
                result["preview"] = self._preview_text(*preview_parts, changes.get_description())
                return result

            self._do(project, root, changes)
            applied_count += 1
            return result
        except Exception:
            if not dry_run and applied_count:
                self._undo(project, applied_count)
            raise
        finally:
            if dry_run and applied_count:
                self._undo(project, applied_count)
            project.close()

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
        validate_identifier(new_name, "python")
        line, column = validate_position_selector(line, column)
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        file_path = self._validate_source_path(file, str(root))

        project = self._get_project(str(root))
        try:
            resource = self._resource_for_existing_path(project, file_path)
            source_code = resource.read()
            candidate = self._resolve_named_candidate(
                source_code,
                old_name,
                line=line,
                column=column,
            )

            renamer = Rename(project, resource, candidate["offset"])
            changes = renamer.get_changes(new_name)
            affected_files = self._merge_paths(
                self._collect_changed_python_files(changes),
                [file],
            )
            result = {
                "success": True,
                "dry_run": dry_run,
                "old_name": old_name,
                "new_name": new_name,
                "file": file,
                "affected_files": affected_files,
            }
            if dry_run:
                result["preview"] = changes.get_description()
                return result

            self._do(project, root, changes)
            return result
        finally:
            project.close()

    def organize_imports(
        self,
        file: str,
        project_root: str,
        dry_run: bool,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Organize imports in a Python module using Rope."""
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        file_path = self._validate_source_path(file, str(root))
        project = self._get_project(str(root))
        try:
            resource = self._resource_for_existing_path(project, file_path)
            organizer = ImportOrganizer(project)
            changes = organizer.organize_imports(resource)
            result = {
                "success": True,
                "dry_run": dry_run,
                "file": file,
                "affected_files": self._merge_paths(self._collect_changed_python_files(changes), [file]),
            }
            if dry_run:
                result["preview"] = changes.get_description()
                return result
            self._do(project, root, changes)
            return result
        finally:
            project.close()

    def extract_variable(
        self,
        *,
        file: str,
        new_name: str,
        start_line: int,
        start_column: int,
        end_line: int,
        end_column: int,
        project_root: str,
        dry_run: bool,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Extract a selected expression into a variable using Rope."""
        validate_identifier(new_name, "python")
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        file_path = self._validate_source_path(file, str(root))
        project = self._get_project(str(root))
        try:
            resource = self._resource_for_existing_path(project, file_path)
            source = resource.read()
            start_offset, end_offset = self._range_to_offsets(
                source,
                start_line,
                start_column,
                end_line,
                end_column,
            )
            extractor = ExtractVariable(project, resource, start_offset, end_offset)
            changes = extractor.get_changes(new_name)
            result = {
                "success": True,
                "dry_run": dry_run,
                "file": file,
                "new_name": new_name,
                "affected_files": self._merge_paths(self._collect_changed_python_files(changes), [file]),
            }
            if dry_run:
                result["preview"] = changes.get_description()
                return result
            self._do(project, root, changes)
            return result
        finally:
            project.close()

    def extract_function(
        self,
        *,
        file: str,
        new_name: str,
        start_line: int,
        start_column: int,
        end_line: int,
        end_column: int,
        project_root: str,
        dry_run: bool,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Extract selected statements into a function using Rope."""
        validate_identifier(new_name, "python")
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        file_path = self._validate_source_path(file, str(root))
        project = self._get_project(str(root))
        try:
            resource = self._resource_for_existing_path(project, file_path)
            source = resource.read()
            start_offset, end_offset = self._range_to_offsets(
                source,
                start_line,
                start_column,
                end_line,
                end_column,
            )
            extractor = ExtractMethod(project, resource, start_offset, end_offset)
            changes = extractor.get_changes(new_name)
            result = {
                "success": True,
                "dry_run": dry_run,
                "file": file,
                "new_name": new_name,
                "affected_files": self._merge_paths(self._collect_changed_python_files(changes), [file]),
            }
            if dry_run:
                result["preview"] = changes.get_description()
                return result
            self._do(project, root, changes)
            return result
        finally:
            project.close()

    def inline_symbol(
        self,
        *,
        file: str,
        line: int,
        column: int,
        project_root: str,
        dry_run: bool,
        expected_git_root: str | None = None,
    ) -> dict[str, Any]:
        """Inline a selected local variable, parameter, or function using Rope.

        Rope substitutes the right-hand side of a variable verbatim into each use
        site without considering operator precedence — e.g. ``s = 2 + 3; s * s``
        becomes ``2 + 3 * 2 + 3`` rather than ``(2 + 3) * (2 + 3)``. Before
        delegating to Rope we wrap the RHS in parentheses when it is not already
        an atom (Name / Constant / Attribute / Subscript / Call / parenthesized).
        """
        line, column = validate_position_selector(line, column)
        if line is None or column is None:
            raise ValueError("inline_symbol requires line and column")
        root, _ = self._prepare_project_root(project_root, expected_git_root)
        file_path = self._validate_source_path(file, str(root))
        project = self._get_project(str(root))
        resource = None
        original_source = None
        did_wrap = False
        source_was_written = False
        applied = False
        try:
            resource = self._resource_for_existing_path(project, file_path)
            original_source = resource.read()

            modified_source, did_wrap = self._wrap_inline_rhs_if_unsafe(
                original_source, line, column,
            )
            if did_wrap and dry_run:
                raise ValueError(
                    "inline_symbol dry-run cannot compute an exact preview when "
                    "operator-precedence wrapping is required; call the MCP tool with apply: true"
                )
            if did_wrap:
                resource.write(modified_source)
                source_was_written = True
                source = modified_source
            else:
                source = original_source

            offset = self._position_to_offset(source, line, column)
            inliner = create_inline(project, resource, offset)
            changes = inliner.get_changes()
            result = {
                "success": True,
                "dry_run": dry_run,
                "file": file,
                "line": line,
                "column": column,
                "affected_files": self._merge_paths(self._collect_changed_python_files(changes), [file]),
            }
            if dry_run:
                result["preview"] = changes.get_description()
                return result
            self._do(project, root, changes)
            applied = True
            return result
        finally:
            if source_was_written and not applied and resource is not None and original_source is not None:
                resource.write(original_source)
            project.close()

    def _wrap_inline_rhs_if_unsafe(
        self, source: str, line: int, column: int,
    ) -> tuple[str, bool]:
        """Return (possibly-wrapped source, did_wrap).

        When the symbol at (line, column) is a variable whose RHS is a composite
        expression (binary op, comparison, conditional, lambda, unary op,
        unparenthesized tuple, etc.), wrap the RHS in parentheses so Rope's
        textual inline preserves the original evaluation order. RHS expressions
        that are already atoms do not need wrapping.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source, False

        try:
            target_offset = self._position_to_offset(source, line, column)
        except ValueError:
            return source, False

        assign_node = self._find_variable_assignment(tree, source, target_offset)
        if assign_node is None:
            return source, False

        rhs = assign_node.value
        if rhs is None:
            return source, False

        if isinstance(rhs, (ast.Name, ast.Constant, ast.Attribute, ast.Subscript, ast.Call)):
            return source, False

        rhs_start = self._position_to_offset(source, rhs.lineno, rhs.col_offset + 1)
        rhs_end = self._position_to_offset(source, rhs.end_lineno, rhs.end_col_offset + 1)
        rhs_text = source[rhs_start:rhs_end]

        if rhs_text.startswith("(") and rhs_text.endswith(")"):
            return source, False

        wrapped = source[:rhs_start] + "(" + rhs_text + ")" + source[rhs_end:]
        return wrapped, True

    def _find_variable_assignment(
        self, tree: ast.AST, source: str, target_offset: int,
    ) -> ast.Assign | ast.AnnAssign | None:
        """Locate the Assign/AnnAssign whose target name contains target_offset."""
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                try:
                    t_start = self._position_to_offset(source, t.lineno, t.col_offset + 1)
                    t_end = self._position_to_offset(source, t.end_lineno, t.end_col_offset + 1)
                except ValueError:
                    continue
                if t_start <= target_offset < t_end:
                    return node
        return None

    def validate_imports(self, project_root: str) -> list[dict[str, Any]]:
        """Check for broken imports in Python files."""
        errors = []
        root = Path(project_root).resolve()
        exports_cache: dict[Path, set[str] | None] = {}
        external_exports_cache: dict[str, set[str] | None] = {}

        if not root.exists():
            return [{"error": f"Project root does not exist: {project_root}", "type": "invalid_root"}]

        for py_file in self._project_python_files_for_scan(root):
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
            except SyntaxError as exc:
                errors.append({
                    "file": str(py_file.relative_to(root)),
                    "line": exc.lineno,
                    "error": str(exc),
                    "type": "syntax_error",
                })
            except Exception as exc:
                logger.warning(f"Error checking {py_file}: {exc}")

        return errors
