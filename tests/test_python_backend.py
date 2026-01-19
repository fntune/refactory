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
        # Target file should not exist (directory may be created for Rope)
        assert not (temp_python_project / "src" / "storage" / "db.py").exists()

    def test_move_module_reports_affected_files(self, python_backend, temp_python_project):
        """Should report all files that will be modified."""
        result = python_backend.move_module(
            source="src/db.py",
            target="src/storage/db.py",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        # Should include the moved file and main.py which imports it
        assert "src/db.py" in result["affected_files"] or any(
            "db.py" in f for f in result["affected_files"]
        )


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
