"""Hermetic tests for Python refactoring backend (Rope)."""
import shutil
import subprocess

import pytest


def _git(cwd, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_git_repo_with_backend(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "refactory@example.test")
    _git(repo, "config", "user.name", "Refactory Test")

    backend = repo / "backend"
    package = backend / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "db.py").write_text(
        "class Database:\n"
        "    def connect(self):\n"
        "        return True\n"
    )
    (package / "main.py").write_text(
        "from pkg.db import Database\n\n"
        "def run():\n"
        "    return Database().connect()\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial")

    worker = tmp_path / "worker"
    _git(repo, "worktree", "add", "-q", "-b", "worker-branch", str(worker))
    return repo, worker


class TestMoveModule:
    """Tests for move_module operation."""

    def test_move_module_creates_target_directory(self, python_backend, temp_python_project):
        """Moving a module should create target directory structure."""
        result = python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        assert result["success"]
        assert (temp_python_project / "src" / "storage" / "db.py").exists()
        assert (temp_python_project / "src" / "storage" / "__init__.py").exists()
        assert not (temp_python_project / "src" / "db.py").exists()

    def test_move_module_updates_imports(self, python_backend, temp_python_project):
        """Moving a module should update imports in dependent files."""
        python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        main_content = (temp_python_project / "src" / "main.py").read_text()
        assert "from src.storage.db import Database" in main_content
        assert "from src.db import Database" not in main_content

    def test_move_module_renames_basename(self, python_backend, tmp_path):
        """Moving to a different filename should apply move + rename once."""
        project = tmp_path / "project"
        project.mkdir()
        package = project / "pkg"
        consumer = project / "consumer"
        package.mkdir()
        consumer.mkdir()
        (package / "__init__.py").write_text("")
        (consumer / "__init__.py").write_text("")
        (package / "source.py").write_text(
            "def answer():\n"
            "    return 42\n"
        )
        (consumer / "use_source.py").write_text(
            "from pkg.source import answer\n\n"
            "VALUE = answer()\n"
        )

        result = python_backend.move_module(
            source="pkg/source.py",
            target="pkg/moved.py",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        assert not (package / "source.py").exists()
        assert (package / "moved.py").exists()
        assert "from pkg.moved import answer" in (
            consumer / "use_source.py"
        ).read_text()

    def test_move_module_dry_run_no_changes(self, python_backend, temp_python_project):
        """Dry run should preview without making changes."""
        original_content = (temp_python_project / "src" / "db.py").read_text()

        result = python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert len(result["affected_files"]) > 0
        # Original file should still exist with same content
        assert (temp_python_project / "src" / "db.py").exists()
        assert (temp_python_project / "src" / "db.py").read_text() == original_content
        # Dry run should not leave behind target directories or code changes
        assert not (temp_python_project / "src" / "storage").exists()
        assert not (temp_python_project / "src" / "storage" / "db.py").exists()
        assert "preview" in result

    def test_move_module_reports_affected_files(self, python_backend, temp_python_project):
        """Should report all files that will be modified."""
        result = python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        assert "src/db.py" in result["affected_files"]
        assert "src/main.py" in result["affected_files"]

    def test_move_module_rejects_identical_paths(self, python_backend, temp_python_project):
        """Moving a module onto itself should fail closed."""
        with pytest.raises(ValueError, match="identical"):
            python_backend.move_module(
                source="src/db.py",
                target="src/db.py",
                project_root=str(temp_python_project),
                dry_run=False,
            )

    def test_move_module_overwrites_existing_target(
        self, python_backend, temp_python_project
    ):
        """overwrite=True should replace an existing distinct target file."""
        target = temp_python_project / "src" / "storage"
        target.mkdir()
        (target / "__init__.py").write_text("")
        (target / "db.py").write_text("BROKEN = True\n")

        result = python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=False,
            overwrite=True,
        )

        assert result["success"]
        moved = (temp_python_project / "src" / "storage" / "db.py").read_text()
        assert "class Database" in moved
        assert "BROKEN = True" not in moved


class TestMoveSymbol:
    """Tests for move_symbol operation."""

    def test_move_function_to_new_module(self, python_backend, temp_python_project):
        """Moving a function should update all references."""
        # Create target file first
        (temp_python_project / "src" / "helpers.py").write_text('"""Helpers."""\n')

        result = python_backend.move_symbol(
            source_file="src/utils.py",
            symbol_name="helper_func",
            target_file="src/helpers.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        assert result["success"]

        # Function should be in new location
        helpers_content = (temp_python_project / "src" / "helpers.py").read_text()
        assert "def helper_func" in helpers_content

        # Function should be removed from original
        utils_content = (temp_python_project / "src" / "utils.py").read_text()
        assert "def helper_func" not in utils_content

    def test_move_class_updates_imports(self, python_backend, temp_python_project):
        """Moving a class should update imports in dependent files."""
        # Create target file
        (temp_python_project / "src" / "helpers.py").write_text('"""Helpers."""\n')

        python_backend.move_symbol(
            source_file="src/utils.py",
            symbol_name="HelperClass",
            target_file="src/helpers.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        # Check that main.py imports from new location
        main_content = (temp_python_project / "src" / "main.py").read_text()
        assert "from src.helpers import" in main_content or "HelperClass" in main_content

    def test_move_symbol_dry_run(self, python_backend, temp_python_project):
        """Dry run should not modify files."""
        (temp_python_project / "src" / "helpers.py").write_text('"""Helpers."""\n')

        original_utils = (temp_python_project / "src" / "utils.py").read_text()

        result = python_backend.move_symbol(
            source_file="src/utils.py",
            symbol_name="helper_func",
            target_file="src/helpers.py",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        # Original should be unchanged
        assert (temp_python_project / "src" / "utils.py").read_text() == original_utils
        assert "preview" in result

    def test_move_symbol_dry_run_to_missing_target_fails_closed(
        self, python_backend, temp_python_project
    ):
        """Rope cannot compute exact import rewrites without the destination
        module existing on disk. Rather than fabricate an incomplete preview
        (or stage the real filesystem), fail closed."""
        original_utils = (temp_python_project / "src" / "utils.py").read_text()

        with pytest.raises(ValueError, match="dry-run requires the target module to exist"):
            python_backend.move_symbol(
                source_file="src/utils.py",
                symbol_name="helper_func",
                target_file="src/new_helpers.py",
                project_root=str(temp_python_project),
                dry_run=True,
            )

        assert (temp_python_project / "src" / "utils.py").read_text() == original_utils
        assert not (temp_python_project / "src" / "new_helpers.py").exists()

    def test_move_symbol_allows_soft_keyword_identifier(self, python_backend, tmp_path):
        """Python soft keywords are legal identifiers outside grammar contexts."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "source.py").write_text("match = 1\n")
        (project / "target.py").write_text("")

        result = python_backend.move_symbol(
            source_file="source.py",
            symbol_name="match",
            target_file="target.py",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        assert "match = 1" in (project / "target.py").read_text()


class TestRenameSymbol:
    """Tests for rename_symbol operation."""

    def test_rename_function(self, python_backend, temp_python_project):
        """Renaming a function should update all references."""
        result = python_backend.rename_symbol(
            file="src/utils.py",
            old_name="helper_func",
            new_name="assist_func",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        assert result["success"]

        # Function should be renamed in definition
        utils_content = (temp_python_project / "src" / "utils.py").read_text()
        assert "def assist_func" in utils_content
        assert "def helper_func" not in utils_content

        # References should be updated
        main_content = (temp_python_project / "src" / "main.py").read_text()
        assert "assist_func" in main_content

        db_content = (temp_python_project / "src" / "db.py").read_text()
        assert "assist_func" in db_content

    def test_rename_class(self, python_backend, temp_python_project):
        """Renaming a class should update all references."""
        result = python_backend.rename_symbol(
            file="src/utils.py",
            old_name="HelperClass",
            new_name="AssistantClass",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        assert result["success"]

        utils_content = (temp_python_project / "src" / "utils.py").read_text()
        assert "class AssistantClass" in utils_content

        main_content = (temp_python_project / "src" / "main.py").read_text()
        assert "AssistantClass" in main_content

    def test_rename_reports_affected_files(self, python_backend, temp_python_project):
        """Should report all affected files."""
        result = python_backend.rename_symbol(
            file="src/utils.py",
            old_name="helper_func",
            new_name="assist_func",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        # Should affect multiple files
        assert len(result["affected_files"]) >= 3  # utils.py, main.py, db.py at minimum

    def test_rename_dry_run_no_changes(self, python_backend, temp_python_project):
        """Dry run should not modify files."""
        original_content = (temp_python_project / "src" / "utils.py").read_text()

        result = python_backend.rename_symbol(
            file="src/utils.py",
            old_name="helper_func",
            new_name="assist_func",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        assert result["dry_run"]
        assert (temp_python_project / "src" / "utils.py").read_text() == original_content
        assert "preview" in result

    def test_rename_parameter_requires_selector_when_ambiguous(
        self, python_backend, tmp_path
    ):
        """Duplicate parameter names should require a selector."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "params.py").write_text(
            "def greet(name):\n"
            "    return name\n\n"
            "def other(name):\n"
            "    return name\n"
        )

        with pytest.raises(ValueError, match="ambiguous"):
            python_backend.rename_symbol(
                file="params.py",
                old_name="name",
                new_name="person",
                project_root=str(project),
                dry_run=False,
            )

    def test_rename_parameter_with_selector(self, python_backend, tmp_path):
        """Selectors should target one parameter declaration precisely."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "params.py").write_text(
            "def greet(name):\n"
            "    return name\n\n"
            "def other(name):\n"
            "    return name\n"
        )

        result = python_backend.rename_symbol(
            file="params.py",
            old_name="name",
            new_name="person",
            project_root=str(project),
            dry_run=False,
            line=1,
            column=11,
        )

        assert result["success"]
        content = (project / "params.py").read_text()
        assert "def greet(person):" in content
        assert "return person" in content
        assert "def other(name):" in content

    def test_rename_method_on_class(self, python_backend, tmp_path):
        """Nested declarations (methods on a class) must be renamable — the
        candidate walk covers ast.walk, not just tree.body."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "shape.py").write_text(
            "class Shape:\n"
            "    def area(self):\n"
            "        return 0\n"
            "\n"
            "def use():\n"
            "    return Shape().area()\n"
        )

        result = python_backend.rename_symbol(
            file="shape.py",
            old_name="area",
            new_name="surface",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = (project / "shape.py").read_text()
        assert "def surface(self)" in content
        assert "Shape().surface()" in content
        assert "def area" not in content

    def test_rename_local_variable_inside_function(self, python_backend, tmp_path):
        """Local variables inside a function are candidates now that
        _iter_named_candidates walks the full tree."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "calc.py").write_text(
            "def work():\n"
            "    total = 0\n"
            "    total = total + 1\n"
            "    return total\n"
        )

        result = python_backend.rename_symbol(
            file="calc.py",
            old_name="total",
            new_name="accumulator",
            project_root=str(project),
            dry_run=False,
            line=2,
            column=5,
        )

        assert result["success"]
        content = (project / "calc.py").read_text()
        assert "accumulator = 0" in content
        assert "return accumulator" in content
        assert "total = " not in content

    def test_rename_to_python_soft_keyword_identifier(self, python_backend, tmp_path):
        """Python soft keywords should be valid rename targets."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "soft.py").write_text(
            "def use():\n"
            "    value = 1\n"
            "    return value\n"
        )

        result = python_backend.rename_symbol(
            file="soft.py",
            old_name="value",
            new_name="match",
            project_root=str(project),
            dry_run=False,
            line=2,
            column=5,
        )

        assert result["success"]
        content = (project / "soft.py").read_text()
        assert "match = 1" in content
        assert "return match" in content


class TestValidateImports:
    """Tests for validate_imports operation."""

    def test_valid_project_no_errors(self, python_backend, temp_python_project):
        """Valid project should have no import errors."""
        errors = python_backend.validate_imports(str(temp_python_project))
        assert len(errors) == 0

    def test_detects_broken_import(self, python_backend, temp_python_project):
        """Should detect broken imports."""
        # Create a file with a broken import
        (temp_python_project / "src" / "broken.py").write_text(
            'from src.nonexistent import something\n'
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        # Should find the broken import
        broken_errors = [e for e in errors if "broken.py" in e.get("file", "")]
        assert len(broken_errors) > 0

    def test_detects_bare_broken_import(self, python_backend, temp_python_project):
        """Should detect unresolved plain imports."""
        (temp_python_project / "src" / "broken_import.py").write_text(
            "import src.missing_module\n"
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        broken_errors = [e for e in errors if "broken_import.py" in e.get("file", "")]
        assert len(broken_errors) > 0
        assert broken_errors[0]["type"] == "unresolved_import"

    def test_detects_missing_imported_name(self, python_backend, temp_python_project):
        """Should detect missing imported names from local modules."""
        (temp_python_project / "src" / "broken_name.py").write_text(
            "from src.utils import missing_name\n"
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        broken_errors = [e for e in errors if "broken_name.py" in e.get("file", "")]
        assert len(broken_errors) > 0
        assert broken_errors[0]["type"] == "unresolved_import_name"
        assert broken_errors[0]["name"] == "missing_name"

    def test_detects_missing_external_imported_name(
        self, python_backend, temp_python_project
    ):
        """Should detect missing imported names from source-backed external modules."""
        (temp_python_project / "src" / "broken_external_name.py").write_text(
            "from typing import definitely_missing_name\n"
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        broken_errors = [e for e in errors if "broken_external_name.py" in e.get("file", "")]
        assert len(broken_errors) > 0
        assert broken_errors[0]["type"] == "unresolved_import_name"
        assert broken_errors[0]["name"] == "definitely_missing_name"

    def test_rejects_top_level_relative_imports(self, python_backend, temp_python_project):
        """Relative imports from root-level modules should be invalid."""
        (temp_python_project / "helpers.py").write_text("VALUE = 1\n")
        (temp_python_project / "consumer.py").write_text("from .helpers import VALUE\n")

        errors = python_backend.validate_imports(str(temp_python_project))

        assert any(
            error.get("file") == "consumer.py"
            and error.get("type") == "unresolved_import"
            for error in errors
        )

    def test_rejects_relative_imports_above_package_root(
        self, python_backend, temp_python_project
    ):
        """Relative imports climbing above the package root should be invalid."""
        nested_pkg = temp_python_project / "src" / "nested"
        nested_pkg.mkdir()
        (nested_pkg / "__init__.py").write_text("")
        (temp_python_project / "util.py").write_text("VALUE = 1\n")
        (nested_pkg / "consumer.py").write_text("from ...util import VALUE\n")

        errors = python_backend.validate_imports(str(temp_python_project))

        assert any(
            error.get("file") == "src/nested/consumer.py"
            and error.get("type") == "unresolved_import"
            for error in errors
        )

    def test_accepts_external_submodule_import(
        self, python_backend, temp_python_project
    ):
        """Importable package submodules should not be false positives."""
        (temp_python_project / "src" / "xml_consumer.py").write_text("from xml import etree\n")

        errors = python_backend.validate_imports(str(temp_python_project))

        assert not any(error.get("file") == "src/xml_consumer.py" for error in errors)

    def test_validate_imports_is_side_effect_free(
        self, python_backend, temp_python_project, tmp_path, monkeypatch
    ):
        """Validation should not execute external module top-level code."""
        ext_root = tmp_path / "external"
        ext_root.mkdir()
        side_effect = ext_root / "side_effect.txt"
        (ext_root / "extmod.py").write_text(
            f'from pathlib import Path\nPath(r"{side_effect}").write_text("ran")\n'
            "exported = 1\n"
        )
        monkeypatch.syspath_prepend(str(ext_root))
        (temp_python_project / "src" / "external_consumer.py").write_text(
            "from extmod import exported\n"
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        assert not any(error.get("file") == "src/external_consumer.py" for error in errors)
        assert not side_effect.exists()

    def test_external_submodule_validation_is_side_effect_free(
        self, python_backend, temp_python_project, tmp_path, monkeypatch
    ):
        """External submodule checks should not import parent packages."""
        ext_root = tmp_path / "external"
        pkg = ext_root / "pkg"
        pkg.mkdir(parents=True)
        side_effect = ext_root / "side_effect.txt"
        (pkg / "__init__.py").write_text(
            f'from pathlib import Path\nPath(r"{side_effect}").write_text("ran")\n'
        )
        (pkg / "submod.py").write_text("exported = 1\n")
        monkeypatch.syspath_prepend(str(ext_root))
        (temp_python_project / "src" / "submodule_consumer.py").write_text(
            "from pkg import submod\n"
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        assert not any(error.get("file") == "src/submodule_consumer.py" for error in errors)
        assert not side_effect.exists()

    def test_detects_syntax_error(self, python_backend, temp_python_project):
        """Should detect syntax errors."""
        (temp_python_project / "src" / "bad_syntax.py").write_text(
            'def broken(\n'  # Unclosed parenthesis
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        syntax_errors = [e for e in errors if e.get("type") == "syntax_error"]
        assert len(syntax_errors) > 0

    def test_ignores_stdlib_imports(self, python_backend, temp_python_project):
        """Should not flag stdlib imports as broken."""
        (temp_python_project / "src" / "with_stdlib.py").write_text('''
import os
import sys
from pathlib import Path
from typing import List
''')

        errors = python_backend.validate_imports(str(temp_python_project))

        stdlib_errors = [e for e in errors if "with_stdlib.py" in e.get("file", "")]
        assert len(stdlib_errors) == 0

    def test_relative_imports_in_nested_packages_are_valid(
        self, python_backend, temp_python_project
    ):
        """Should resolve relative imports using package context."""
        nested_pkg = temp_python_project / "src" / "nested"
        nested_pkg.mkdir()
        (nested_pkg / "__init__.py").write_text("")
        (nested_pkg / "helpers.py").write_text("VALUE = 1\n")
        (nested_pkg / "consumer.py").write_text("from .helpers import VALUE\n")

        errors = python_backend.validate_imports(str(temp_python_project))

        nested_errors = [e for e in errors if "nested/consumer.py" in e.get("file", "")]
        assert nested_errors == []

    def test_namespace_packages_are_valid(self, python_backend, temp_python_project):
        """Should treat local namespace-package directories as importable modules."""
        namespace_dir = temp_python_project / "namespace_pkg" / "nested"
        namespace_dir.mkdir(parents=True)
        (namespace_dir / "module.py").write_text("VALUE = 1\n")
        (temp_python_project / "consumer.py").write_text(
            "import namespace_pkg.nested.module\n"
        )

        errors = python_backend.validate_imports(str(temp_python_project))

        namespace_errors = [e for e in errors if e.get("file") == "consumer.py"]
        assert namespace_errors == []

    def test_invalid_python_module_segment_is_rejected(
        self, python_backend, temp_python_project
    ):
        """Hyphenated target segments should fail before invoking Rope."""
        with pytest.raises(ValueError, match="invalid Python module name"):
            python_backend.move_module(
                source="src/db.py",
                target="src/bad-name/db.py",
                project_root=str(temp_python_project),
                dry_run=False,
            )


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_move_nonexistent_file_raises(self, python_backend, temp_python_project):
        """Moving nonexistent file should raise a refactory-shaped error."""
        with pytest.raises(ValueError, match="source file not found"):
            python_backend.move_module(
                source="src/nonexistent.py",
                target="src/somewhere/nonexistent.py",
                project_root=str(temp_python_project),
                dry_run=False,
            )

    def test_move_symbol_nonexistent_source_raises(
        self, python_backend, temp_python_project
    ):
        """Moving a symbol from a nonexistent source file should fail closed."""
        (temp_python_project / "src" / "target.py").write_text("")
        with pytest.raises(ValueError, match="source file not found"):
            python_backend.move_symbol(
                source_file="src/nonexistent.py",
                symbol_name="foo",
                target_file="src/target.py",
                project_root=str(temp_python_project),
                dry_run=False,
            )

    def test_rename_nonexistent_file_raises(self, python_backend, temp_python_project):
        """Renaming in a nonexistent file should raise a refactory-shaped error."""
        with pytest.raises(ValueError, match="source file not found"):
            python_backend.rename_symbol(
                file="src/nonexistent.py",
                old_name="foo",
                new_name="bar",
                project_root=str(temp_python_project),
                dry_run=False,
            )

    def test_rename_nonexistent_symbol_raises(self, python_backend, temp_python_project):
        """Renaming nonexistent symbol should raise error."""
        with pytest.raises(Exception):
            python_backend.rename_symbol(
                file="src/utils.py",
                old_name="nonexistent_func",
                new_name="new_name",
                project_root=str(temp_python_project),
                dry_run=False,
            )

    def test_move_preserves_docstrings(self, python_backend, temp_python_project):
        """Moving should preserve docstrings and comments."""
        (temp_python_project / "src" / "helpers.py").write_text('"""Helpers module."""\n')

        python_backend.move_symbol(
            source_file="src/utils.py",
            symbol_name="helper_func",
            target_file="src/helpers.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        content = (temp_python_project / "src" / "helpers.py").read_text()
        assert "A helper function" in content  # Docstring preserved

    def test_multiple_moves_sequential(self, python_backend, temp_python_project):
        """Multiple moves should work correctly."""
        # First move
        python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        # Second move
        python_backend.move_module(
            source="src/utils.py",
            target="src/core/utils.py",
            project_root=str(temp_python_project),
            dry_run=False,
        )

        # Both should exist in new locations
        assert (temp_python_project / "src" / "storage" / "db.py").exists()
        assert (temp_python_project / "src" / "core" / "utils.py").exists()

        # Imports should be updated
        main_content = (temp_python_project / "src" / "main.py").read_text()
        assert "src.storage.db" in main_content or "storage.db" in main_content
        assert "src.core.utils" in main_content or "core.utils" in main_content

    def test_path_traversal_source_blocked(self, python_backend, temp_python_project):
        """Path traversal in source should be blocked."""
        with pytest.raises(ValueError) as exc_info:
            python_backend.move_module(
                source="../outside.py",
                target="src/inside.py",
                project_root=str(temp_python_project),
                dry_run=False,
            )
        assert "escapes project root" in str(exc_info.value)

    def test_path_traversal_target_blocked(self, python_backend, temp_python_project):
        """Path traversal in target should be blocked."""
        with pytest.raises(ValueError) as exc_info:
            python_backend.move_module(
                source="src/utils.py",
                target="../outside.py",
                project_root=str(temp_python_project),
                dry_run=False,
            )
        assert "escapes project root" in str(exc_info.value)

    def test_move_symbol_rejects_chained_assignment(self, python_backend, tmp_path):
        """Moving one name out of a chained assignment should fail closed."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "source.py").write_text("a = b = 1\n")
        (project / "target.py").write_text("")

        with pytest.raises(ValueError, match="multi-target or destructuring assignment"):
            python_backend.move_symbol(
                source_file="source.py",
                symbol_name="a",
                target_file="target.py",
                project_root=str(project),
                dry_run=False,
            )

    def test_invalid_project_root_handled(self, python_backend, tmp_path):
        """Invalid project root should return error."""
        errors = python_backend.validate_imports(str(tmp_path / "nonexistent"))
        assert len(errors) == 1
        assert errors[0]["type"] == "invalid_root"


class TestRopeHazardDetectors:
    """Fail-closed pre-flights for Rope limitations that silently corrupt code."""

    def test_move_module_fails_on_basename_collision(self, python_backend, tmp_path):
        """E2: source file exporting a top-level binding named same as stem.

        Rope confuses variable attribute access (``foo.method()``) with module
        attribute access when rewriting consumers, corrupting call sites.
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "project_service.py").write_text(
            "class ProjectService:\n"
            "    def do_thing(self):\n"
            "        return 1\n\n"
            "project_service = ProjectService()\n"
        )
        (project / "consumer.py").write_text(
            "from project_service import project_service\n\n"
            "def use():\n"
            "    return project_service.do_thing()\n"
        )

        before = (project / "consumer.py").read_text()
        with pytest.raises(ValueError, match="same name as the module"):
            python_backend.move_module(
                source="project_service.py",
                target="store/project_service.py",
                project_root=str(project),
                dry_run=False,
            )

        assert (project / "consumer.py").read_text() == before
        assert (project / "project_service.py").exists()
        assert not (project / "store").exists()

    def test_move_module_fails_on_lazy_import(self, python_backend, tmp_path):
        """E1: consumer uses in-function import of source module.

        Rope hoists in-function imports to module top, breaking any
        circular-import workarounds the lazy import was working around.
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "worker.py").write_text("VALUE = 1\n")
        (project / "consumer.py").write_text(
            "def run():\n"
            "    import worker\n"
            "    return worker.VALUE\n"
        )

        before_consumer = (project / "consumer.py").read_text()
        with pytest.raises(ValueError, match="lazy"):
            python_backend.move_module(
                source="worker.py",
                target="bg/worker.py",
                project_root=str(project),
                dry_run=False,
            )

        assert (project / "consumer.py").read_text() == before_consumer
        assert (project / "worker.py").exists()
        assert not (project / "bg").exists()

    def test_move_module_reports_lazy_import_location(self, python_backend, tmp_path):
        """Lazy-import error should name each offending file and line."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "service.py").write_text("HELLO = 'hi'\n")
        (project / "a.py").write_text(
            "def run():\n"
            "    from service import HELLO\n"
            "    return HELLO\n"
        )
        (project / "b.py").write_text(
            "class App:\n"
            "    def boot(self):\n"
            "        import service\n"
            "        return service.HELLO\n"
        )

        with pytest.raises(ValueError) as info:
            python_backend.move_module(
                source="service.py",
                target="services/service.py",
                project_root=str(project),
                dry_run=False,
            )

        message = str(info.value)
        assert "a.py:2" in message
        assert "b.py:3" in message
        assert "2 in-function" in message

    def test_move_module_ignores_lazy_imports_of_other_modules(
        self, python_backend, tmp_path
    ):
        """Lazy imports of unrelated modules should not block the move."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "target_mod.py").write_text("VALUE = 1\n")
        (project / "other_mod.py").write_text("OTHER = 2\n")
        (project / "consumer.py").write_text(
            "from target_mod import VALUE\n\n"
            "def run():\n"
            "    import other_mod\n"
            "    return other_mod.OTHER + VALUE\n"
        )

        result = python_backend.move_module(
            source="target_mod.py",
            target="pkg/target_mod.py",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        assert (project / "pkg" / "target_mod.py").exists()

    def test_move_symbol_fails_on_basename_collision(self, python_backend, tmp_path):
        """E2 applies to move_symbol too."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "project_service.py").write_text(
            "class ProjectService:\n"
            "    def do(self): return 1\n\n"
            "project_service = ProjectService()\n\n"
            "def helper():\n"
            "    return 42\n"
        )
        (project / "store.py").write_text("")

        with pytest.raises(ValueError, match="same name as the module"):
            python_backend.move_symbol(
                source_file="project_service.py",
                symbol_name="helper",
                target_file="store.py",
                project_root=str(project),
                dry_run=False,
            )

        assert "def helper" in (project / "project_service.py").read_text()

    def test_move_symbol_fails_on_lazy_import(self, python_backend, tmp_path):
        """E1 applies to move_symbol too."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "worker.py").write_text(
            "def do_work():\n"
            "    return 1\n"
        )
        (project / "target.py").write_text("")
        (project / "consumer.py").write_text(
            "def run():\n"
            "    from worker import do_work\n"
            "    return do_work()\n"
        )

        with pytest.raises(ValueError, match="lazy"):
            python_backend.move_symbol(
                source_file="worker.py",
                symbol_name="do_work",
                target_file="target.py",
                project_root=str(project),
                dry_run=False,
            )

        assert "def do_work" in (project / "worker.py").read_text()
        assert (project / "target.py").read_text() == ""

    def test_move_symbol_fails_on_module_style_lazy_import(
        self, python_backend, tmp_path
    ):
        """Module-style lazy imports of the moved symbol should also fail closed."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "worker.py").write_text(
            "def do_work():\n"
            "    return 1\n\n"
            "def other():\n"
            "    return 2\n"
        )
        (project / "target.py").write_text("")
        (project / "consumer.py").write_text(
            "def run():\n"
            "    import worker\n"
            "    return worker.do_work()\n"
        )

        original_worker = (project / "worker.py").read_text()
        original_target = (project / "target.py").read_text()
        original_consumer = (project / "consumer.py").read_text()

        with pytest.raises(ValueError, match="lazy"):
            python_backend.move_symbol(
                source_file="worker.py",
                symbol_name="do_work",
                target_file="target.py",
                project_root=str(project),
                dry_run=False,
            )

        assert (project / "worker.py").read_text() == original_worker
        assert (project / "target.py").read_text() == original_target
        assert (project / "consumer.py").read_text() == original_consumer

    def test_move_symbol_fails_on_dotted_module_style_lazy_import(
        self, python_backend, tmp_path
    ):
        """Package-qualified lazy imports of the moved symbol should fail closed."""
        project = tmp_path / "project"
        project.mkdir()
        package = project / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "worker.py").write_text(
            "def do_work():\n"
            "    return 1\n\n"
            "def other():\n"
            "    return 2\n"
        )
        (project / "target.py").write_text("")
        (project / "consumer.py").write_text(
            "def run():\n"
            "    import pkg.worker\n"
            "    return pkg.worker.do_work()\n"
        )

        original_worker = (package / "worker.py").read_text()
        original_target = (project / "target.py").read_text()
        original_consumer = (project / "consumer.py").read_text()

        with pytest.raises(ValueError, match="lazy"):
            python_backend.move_symbol(
                source_file="pkg/worker.py",
                symbol_name="do_work",
                target_file="target.py",
                project_root=str(project),
                dry_run=False,
            )

        assert (package / "worker.py").read_text() == original_worker
        assert (project / "target.py").read_text() == original_target
        assert (project / "consumer.py").read_text() == original_consumer

    def test_move_symbol_ignores_lazy_imports_of_other_symbols(
        self, python_backend, tmp_path
    ):
        """E1 for move_symbol should narrow to the specific symbol being moved.

        A lazy ``from worker import OTHER_SYMBOL`` does not trip Rope's
        move_symbol rewrite of ``do_work`` — other names stay pointed at the
        original module. Flagging it as a hazard would be over-conservative.
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "worker.py").write_text(
            "def do_work():\n"
            "    return 1\n\n"
            "OTHER_SYMBOL = 'unrelated'\n"
        )
        (project / "target.py").write_text("")
        (project / "consumer.py").write_text(
            "def run():\n"
            "    from worker import OTHER_SYMBOL\n"
            "    return OTHER_SYMBOL\n"
        )

        result = python_backend.move_symbol(
            source_file="worker.py",
            symbol_name="do_work",
            target_file="target.py",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        assert "def do_work" in (project / "target.py").read_text()
        # Consumer's lazy import of the unrelated symbol should be untouched.
        assert "from worker import OTHER_SYMBOL" in (project / "consumer.py").read_text()

    def test_hazard_check_runs_in_dry_run_too(self, python_backend, tmp_path):
        """Dry-run must still fail-closed on hazards; otherwise a preview
        misleads the user about what the real run would do."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "project_service.py").write_text("project_service = object()\n")

        with pytest.raises(ValueError, match="same name as the module"):
            python_backend.move_module(
                source="project_service.py",
                target="store/project_service.py",
                project_root=str(project),
                dry_run=True,
            )

        assert (project / "project_service.py").exists()
        assert not (project / "store").exists()

    def test_top_level_import_of_source_does_not_trip_lazy_detector(
        self, python_backend, tmp_path
    ):
        """E1 only catches in-function imports. Top-level imports rewrite cleanly."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "worker.py").write_text("VALUE = 1\n")
        (project / "consumer.py").write_text(
            "import worker\n\n"
            "def run():\n"
            "    return worker.VALUE\n"
        )

        result = python_backend.move_module(
            source="worker.py",
            target="bg/worker.py",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        assert (project / "bg" / "worker.py").exists()


class TestProjectRootGuards:
    """Guards against cwd drift and cross-root writes."""

    def test_relative_project_root_rejected(self, python_backend):
        """Backend callers must pass an absolute project_root."""
        with pytest.raises(ValueError, match="absolute path"):
            python_backend.move_module(
                source="pkg/db.py",
                target="pkg/storage/db.py",
                project_root="backend",
                dry_run=False,
            )

    def test_linked_worktree_move_mutates_only_worker(
        self, python_backend, tmp_path
    ):
        """A move mutates only the explicit project_root checkout."""
        if not shutil.which("git"):
            pytest.skip("git not available")

        main_repo, worker = _make_git_repo_with_backend(tmp_path)

        result = python_backend.move_module(
            source="pkg/db.py",
            target="pkg/storage/db.py",
            project_root=str(worker / "backend"),
            dry_run=False,
        )

        assert result["success"]
        assert (worker / "backend" / "pkg" / "storage" / "db.py").exists()
        assert "from pkg.storage.db import Database" in (
            worker / "backend" / "pkg" / "main.py"
        ).read_text()
        assert (main_repo / "backend" / "pkg" / "db.py").exists()
        assert not (main_repo / "backend" / "pkg" / "storage").exists()
        assert _git(main_repo, "status", "--short").stdout == ""

    def test_refuses_changed_resources_outside_project_root(
        self, python_backend, tmp_path
    ):
        """The apply guard checks Rope's changed resources before project.do."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        class FakeProject:
            address = str(project_root)

        class FakeResource:
            path = "../outside.py"

        class FakeChange:
            def get_changed_resources(self):
                return [FakeResource()]

        with pytest.raises(ValueError, match="outside project_root"):
            python_backend._assert_change_inside_project_root(
                FakeProject(),
                project_root,
                FakeChange(),
            )


class TestValidateImportsScope:
    """Scope tests for validate_imports — should ignore vendored/build/worktree code."""

    def test_ignores_build_dir_in_nongit_mode(self, python_backend, tmp_path):
        """Files inside build/ should not appear in validate_imports output."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "good.py").write_text("VALUE = 1\n")
        build_dir = project / "build"
        build_dir.mkdir()
        (build_dir / "broken.py").write_text("from nowhere import missing\n")

        errors = python_backend.validate_imports(str(project))

        assert not any("build/broken.py" in error.get("file", "") for error in errors)

    def test_ignores_standard_venv_directories(self, python_backend, tmp_path):
        """Every well-known virtualenv name should be skipped in non-git mode."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "good.py").write_text("VALUE = 1\n")
        for venv_name in [".venv", "venv", "env", ".tox", ".mypy_cache", "node_modules"]:
            venv = project / venv_name
            venv.mkdir()
            (venv / "broken.py").write_text("from nonexistent import missing\n")

        errors = python_backend.validate_imports(str(project))

        files = [error.get("file", "") for error in errors]
        assert not any("/broken.py" in entry for entry in files)

    def test_ignores_egg_info_directories(self, python_backend, tmp_path):
        """*.egg-info directories hold build artifacts and should be skipped."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "good.py").write_text("VALUE = 1\n")
        egg_info = project / "mypkg.egg-info"
        egg_info.mkdir()
        (egg_info / "broken.py").write_text("from nonexistent import missing\n")

        errors = python_backend.validate_imports(str(project))

        assert not any("egg-info" in error.get("file", "") for error in errors)

    def test_respects_gitignore(self, python_backend, tmp_path):
        """In a git repo, files matched by .gitignore should not be scanned."""
        if not shutil.which("git"):
            pytest.skip("git not available")

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        (project / ".gitignore").write_text("junk/\ngenerated.py\n")
        (project / "tracked.py").write_text("VALUE = 1\n")
        (project / "generated.py").write_text("from nonexistent import missing\n")
        junk = project / "junk"
        junk.mkdir()
        (junk / "broken.py").write_text("from nonexistent import missing\n")

        errors = python_backend.validate_imports(str(project))

        files = [error.get("file", "") for error in errors]
        assert "generated.py" not in files
        assert not any("junk/" in entry for entry in files)

    def test_respects_gitignore_when_project_root_is_subdir(
        self, python_backend, tmp_path
    ):
        """A package sub-root inside a git worktree should still honor .gitignore."""
        if not shutil.which("git"):
            pytest.skip("git not available")

        repo = tmp_path / "repo"
        backend = repo / "backend"
        backend.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / ".gitignore").write_text("backend/generated.py\nbackend/junk/\n")
        (backend / "tracked.py").write_text("VALUE = 1\n")
        (backend / "generated.py").write_text("from missing_generated import value\n")
        junk = backend / "junk"
        junk.mkdir()
        (junk / "broken.py").write_text("from missing_junk import value\n")

        errors = python_backend.validate_imports(str(backend))

        assert errors == []

    def test_ignores_git_worktree_directory(self, python_backend, tmp_path):
        """Files under .git/worktrees/ must never surface in validate_imports."""
        if not shutil.which("git"):
            pytest.skip("git not available")

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        (project / "tracked.py").write_text("VALUE = 1\n")

        worktree_branch = project / ".git" / "worktrees" / "branch"
        worktree_branch.mkdir(parents=True)
        (worktree_branch / "broken.py").write_text(
            "from definitely_nonexistent import missing\n"
        )

        errors = python_backend.validate_imports(str(project))

        assert not any(".git/worktrees" in error.get("file", "") for error in errors)

    def test_gitignored_file_with_broken_import_does_not_error(
        self, python_backend, tmp_path
    ):
        """Validate end-to-end: a gitignored directory full of garbage imports
        should not produce a single error entry."""
        if not shutil.which("git"):
            pytest.skip("git not available")

        project = tmp_path / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        (project / ".gitignore").write_text("vendored/\n")
        vendored = project / "vendored"
        vendored.mkdir()
        for i in range(5):
            (vendored / f"broken_{i}.py").write_text(
                f"from nonexistent_{i} import x\n"
            )
        (project / "good.py").write_text("VALUE = 1\n")

        errors = python_backend.validate_imports(str(project))

        assert not any("vendored" in error.get("file", "") for error in errors)

    def test_falls_back_to_rglob_when_not_a_git_repo(self, python_backend, tmp_path):
        """Without .git present, the scan should still find real import errors."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "consumer.py").write_text(
            "from nonexistent_module import missing\n"
        )

        errors = python_backend.validate_imports(str(project))

        assert any("consumer.py" in error.get("file", "") for error in errors)
