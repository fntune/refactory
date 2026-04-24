"""Hermetic tests for Python refactoring backend (Rope)."""
import pytest


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
        """Moving nonexistent file should raise error."""
        with pytest.raises(Exception):
            python_backend.move_module(
                source="src/nonexistent.py",
                target="src/somewhere/nonexistent.py",
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
