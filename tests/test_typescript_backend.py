"""Hermetic tests for TypeScript refactoring backend (ts-morph)."""
from pathlib import Path

import pytest

# Check if ts-morph is available
TSMORPH_SCRIPT = Path(__file__).parent.parent / "server" / "tsmorph" / "refactor.js"
TSMORPH_AVAILABLE = (
    TSMORPH_SCRIPT.exists()
    and (TSMORPH_SCRIPT.parent / "node_modules").exists()
)

pytestmark = pytest.mark.skipif(
    not TSMORPH_AVAILABLE,
    reason="ts-morph not installed. Run: cd server/tsmorph && pnpm install"
)


class TestMoveModule:
    """Tests for move_module operation."""

    def test_move_module_creates_target_directory(self, typescript_backend, temp_typescript_project):
        """Moving a module should create target directory structure."""
        result = typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]
        assert (temp_typescript_project / "src" / "storage" / "db.ts").exists()
        assert not (temp_typescript_project / "src" / "db.ts").exists()

    def test_move_module_updates_imports(self, typescript_backend, temp_typescript_project):
        """Moving a module should update imports in dependent files."""
        typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        main_content = (temp_typescript_project / "src" / "main.ts").read_text()
        # Import path should be updated
        assert "./storage/db" in main_content or "storage/db" in main_content

    def test_move_module_updates_commonjs_require(
        self, typescript_backend, temp_typescript_project
    ):
        """Moving a module should update relative require consumers."""
        (temp_typescript_project / "src" / "consumer.ts").write_text(
            'const { Database } = require("./db");\n'
        )

        typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        consumer_content = (temp_typescript_project / "src" / "consumer.ts").read_text()
        assert './storage/db' in consumer_content

    def test_move_module_updates_re_exports(self, typescript_backend, temp_typescript_project):
        """Moving a module should update re-export declarations."""
        (temp_typescript_project / "src" / "index.ts").write_text(
            'export * from "./db";\n'
        )

        typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        index_content = (temp_typescript_project / "src" / "index.ts").read_text()
        assert './storage/db' in index_content

    def test_move_module_dry_run_no_changes(self, typescript_backend, temp_typescript_project):
        """Dry run should preview without making changes."""
        result = typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        # Original file should still exist
        assert (temp_typescript_project / "src" / "db.ts").exists()

    def test_move_module_reports_affected_files(self, typescript_backend, temp_typescript_project):
        """Should report all files that will be modified."""
        result = typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=True,
        )

        assert "affected_files" in result
        assert len(result["affected_files"]) > 0


class TestMoveSymbol:
    """Tests for move_symbol operation."""

    def test_move_function_to_new_module(self, typescript_backend, temp_typescript_project):
        """Moving a function should update all references."""
        # Create target file
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        result = typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="helperFunc",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]

        # Function should be in new location
        helpers_content = (temp_typescript_project / "src" / "helpers.ts").read_text()
        assert "helperFunc" in helpers_content

        utils_content = (temp_typescript_project / "src" / "utils.ts").read_text()
        assert 'import { helperFunc } from "./helpers";' in utils_content
        assert 'export { helperFunc } from "./helpers";' in utils_content

    def test_move_variable_to_new_module(self, typescript_backend, temp_typescript_project):
        """Moving a variable should preserve a valid declaration in the target file."""
        (temp_typescript_project / "src" / "constants.ts").write_text(
            "export const answer = 42;\n"
        )
        (temp_typescript_project / "src" / "consumer.ts").write_text(
            'import { answer } from "./constants";\n'
            "export const value = answer;\n"
        )
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        result = typescript_backend.move_symbol(
            source_file="src/constants.ts",
            symbol_name="answer",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]

        helpers_content = (temp_typescript_project / "src" / "helpers.ts").read_text()
        consumer_content = (temp_typescript_project / "src" / "consumer.ts").read_text()

        assert "export const answer = 42;" in helpers_content
        assert 'import { answer } from "./helpers"' in consumer_content

    def test_move_class(self, typescript_backend, temp_typescript_project):
        """Moving a class should work correctly."""
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        result = typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="HelperClass",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]

        helpers_content = (temp_typescript_project / "src" / "helpers.ts").read_text()
        assert "HelperClass" in helpers_content

    def test_move_symbol_preserves_alias_imports(
        self, typescript_backend, temp_typescript_project
    ):
        """Moving a symbol should preserve aliased import consumers."""
        (temp_typescript_project / "src" / "main.ts").write_text(
            'import { helperFunc as help } from "./utils";\n'
            "export const value = help();\n"
        )
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="helperFunc",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        main_content = (temp_typescript_project / "src" / "main.ts").read_text()
        helpers_content = (temp_typescript_project / "src" / "helpers.ts").read_text()

        assert 'import { helperFunc as help } from "./helpers"' in main_content
        assert "help();" in main_content
        assert "export export" not in helpers_content

    def test_move_symbol_preserves_aliases_when_target_import_exists(
        self, typescript_backend, temp_typescript_project
    ):
        """Moving a symbol should preserve alias metadata when merging into an existing import."""
        (temp_typescript_project / "src" / "helpers.ts").write_text(
            'export function otherFunc(): string {\n'
            '    return "x";\n'
            "}\n"
        )
        (temp_typescript_project / "src" / "main.ts").write_text(
            'import { otherFunc } from "./helpers";\n'
            'import { helperFunc as help } from "./utils";\n'
            "export const value = help() + otherFunc();\n"
        )

        typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="helperFunc",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        main_content = (temp_typescript_project / "src" / "main.ts").read_text()
        assert 'import { otherFunc, helperFunc as help } from "./helpers"' in main_content
        assert "help() + otherFunc()" in main_content

    def test_move_symbol_updates_re_exports(self, typescript_backend, temp_typescript_project):
        """Moving a symbol should update re-export declarations."""
        (temp_typescript_project / "src" / "index.ts").write_text(
            'export { helperFunc } from "./utils";\n'
        )
        (temp_typescript_project / "src" / "consumer.ts").write_text(
            'const { helperFunc } = require("./utils");\n'
            "export const value = helperFunc();\n"
        )
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="helperFunc",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        index_content = (temp_typescript_project / "src" / "index.ts").read_text()
        utils_content = (temp_typescript_project / "src" / "utils.ts").read_text()
        assert 'export { helperFunc } from "./helpers"' in index_content
        assert 'export { helperFunc } from "./helpers";' in utils_content

    def test_move_symbol_preserves_type_only_imports(
        self, typescript_backend, temp_typescript_project
    ):
        """Moving a type alias should preserve type-only imports."""
        (temp_typescript_project / "src" / "types.ts").write_text(
            "export type Helper = {\n"
            "    value: string;\n"
            "};\n"
        )
        (temp_typescript_project / "src" / "consumer.ts").write_text(
            'import { type Helper } from "./types";\n'
            'export const value: Helper = { value: "ok" };\n'
        )
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        typescript_backend.move_symbol(
            source_file="src/types.ts",
            symbol_name="Helper",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        consumer_content = (temp_typescript_project / "src" / "consumer.ts").read_text()
        assert 'import { type Helper } from "./helpers"' in consumer_content

    def test_move_symbol_dry_run(self, typescript_backend, temp_typescript_project):
        """Dry run should not modify files."""
        (temp_typescript_project / "src" / "helpers.ts").write_text("")
        original_utils = (temp_typescript_project / "src" / "utils.ts").read_text()

        result = typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="helperFunc",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=True,
        )

        assert result["success"]
        # Original should be unchanged
        assert (temp_typescript_project / "src" / "utils.ts").read_text() == original_utils


class TestRenameSymbol:
    """Tests for rename_symbol operation."""

    def test_rename_function(self, typescript_backend, temp_typescript_project):
        """Renaming a function should update all references."""
        result = typescript_backend.rename_symbol(
            file="src/utils.ts",
            old_name="helperFunc",
            new_name="assistFunc",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]

        # Function should be renamed
        utils_content = (temp_typescript_project / "src" / "utils.ts").read_text()
        assert "assistFunc" in utils_content
        assert "function helperFunc" not in utils_content

    def test_rename_class(self, typescript_backend, temp_typescript_project):
        """Renaming a class should update all references."""
        result = typescript_backend.rename_symbol(
            file="src/utils.ts",
            old_name="HelperClass",
            new_name="AssistantClass",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]

        utils_content = (temp_typescript_project / "src" / "utils.ts").read_text()
        assert "AssistantClass" in utils_content

        # Check imports updated
        main_content = (temp_typescript_project / "src" / "main.ts").read_text()
        assert "AssistantClass" in main_content

    def test_rename_dry_run_no_changes(self, typescript_backend, temp_typescript_project):
        """Dry run should not modify files."""
        original = (temp_typescript_project / "src" / "utils.ts").read_text()

        typescript_backend.rename_symbol(
            file="src/utils.ts",
            old_name="helperFunc",
            new_name="assistFunc",
            project_root=str(temp_typescript_project),
            dry_run=True,
        )

        assert (temp_typescript_project / "src" / "utils.ts").read_text() == original


class TestValidateImports:
    """Tests for validate_imports operation."""

    def test_valid_project_no_errors(self, typescript_backend, temp_typescript_project):
        """Valid project should have no import errors."""
        errors = typescript_backend.validate_imports(str(temp_typescript_project))
        assert len(errors) == 0

    def test_detects_broken_import(self, typescript_backend, temp_typescript_project):
        """Should detect broken imports."""
        (temp_typescript_project / "src" / "broken.ts").write_text(
            'import { something } from "./nonexistent";\n'
        )

        errors = typescript_backend.validate_imports(str(temp_typescript_project))
        assert len(errors) > 0


class TestEdgeCases:
    """Tests for edge cases."""

    def test_move_nonexistent_symbol_raises(self, typescript_backend, temp_typescript_project):
        """Moving nonexistent symbol should raise error."""
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        with pytest.raises(Exception):
            typescript_backend.move_symbol(
                source_file="src/utils.ts",
                symbol_name="nonExistentFunc",
                target_file="src/helpers.ts",
                project_root=str(temp_typescript_project),
                dry_run=False,
            )

    def test_rename_nonexistent_symbol_raises(self, typescript_backend, temp_typescript_project):
        """Renaming nonexistent symbol should raise error."""
        with pytest.raises(Exception):
            typescript_backend.rename_symbol(
                file="src/utils.ts",
                old_name="nonExistentFunc",
                new_name="newName",
                project_root=str(temp_typescript_project),
                dry_run=False,
            )

    def test_handles_tsx_files(self, typescript_backend, temp_typescript_project):
        """Should handle TSX files correctly."""
        (temp_typescript_project / "src" / "component.tsx").write_text('''
import { helperFunc } from "./utils";

export function Component() {
    return <div>{helperFunc()}</div>;
}
''')

        result = typescript_backend.rename_symbol(
            file="src/utils.ts",
            old_name="helperFunc",
            new_name="assistFunc",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        assert result["success"]

        # TSX file should be updated
        tsx_content = (temp_typescript_project / "src" / "component.tsx").read_text()
        assert "assistFunc" in tsx_content

    def test_path_traversal_blocked(self, typescript_backend, temp_typescript_project):
        """Path traversal should be blocked."""
        with pytest.raises(Exception) as exc_info:
            typescript_backend.move_module(
                source="../outside.ts",
                target="src/inside.ts",
                project_root=str(temp_typescript_project),
                dry_run=False,
            )
        assert "escapes project root" in str(exc_info.value)

    def test_move_symbol_updates_imports_in_consumers(self, typescript_backend, temp_typescript_project):
        """Moving a symbol should update imports in files that use it."""
        # Create target file
        (temp_typescript_project / "src" / "helpers.ts").write_text("")

        # Move helperFunc from utils to helpers
        typescript_backend.move_symbol(
            source_file="src/utils.ts",
            symbol_name="helperFunc",
            target_file="src/helpers.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
        )

        # Check that main.ts now imports from helpers instead of utils
        main_content = (temp_typescript_project / "src" / "main.ts").read_text()
        # Should import helperFunc from helpers
        assert "helpers" in main_content
        # Should no longer have helperFunc import from utils
        utils_imports = [line for line in main_content.split("\n") if "utils" in line and "helperFunc" in line]
        assert len(utils_imports) == 0
