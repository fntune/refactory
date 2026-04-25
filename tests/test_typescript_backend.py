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
        assert "preview" in result
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
        assert "src/storage/db.ts" in result["affected_files"]

    def test_move_module_updates_directory_requires_to_index(
        self, typescript_backend, tmp_path
    ):
        """Directory-style require paths should resolve to index files."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "db").mkdir()
        (project / "src" / "db" / "index.ts").write_text("export const value = 1;\n")
        (project / "src" / "consumer.ts").write_text(
            'const { value } = require("./db");\n'
            "export const output = value;\n"
        )

        result = typescript_backend.move_module(
            source="src/db/index.ts",
            target="src/core/db/index.ts",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        consumer = (project / "src" / "consumer.ts").read_text()
        assert 'require("./core/db/index")' in consumer
        assert "src/consumer.ts" in result["affected_files"]

    def test_move_module_rejects_identical_paths(self, typescript_backend, temp_typescript_project):
        """Moving a module onto itself should fail closed."""
        with pytest.raises(Exception, match="identical"):
            typescript_backend.move_module(
                source="src/db.ts",
                target="src/db.ts",
                project_root=str(temp_typescript_project),
                dry_run=False,
            )

    def test_move_module_overwrites_existing_target(
        self, typescript_backend, temp_typescript_project
    ):
        """overwrite=True should replace an existing distinct target file."""
        target = temp_typescript_project / "src" / "storage"
        target.mkdir()
        (target / "db.ts").write_text("export const BROKEN = true;\n")

        result = typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=False,
            overwrite=True,
        )

        assert result["success"]
        moved = (temp_typescript_project / "src" / "storage" / "db.ts").read_text()
        assert "class Database" in moved
        assert "BROKEN" not in moved

    def test_move_module_dry_run_overwrite_preserves_existing_target(
        self, typescript_backend, temp_typescript_project
    ):
        """dry_run with overwrite=True should not delete or replace the target."""
        target = temp_typescript_project / "src" / "storage"
        target.mkdir()
        target_file = target / "db.ts"
        original_target = "export const BROKEN = true;\n"
        target_file.write_text(original_target)

        result = typescript_backend.move_module(
            source="src/db.ts",
            target="src/storage/db.ts",
            project_root=str(temp_typescript_project),
            dry_run=True,
            overwrite=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert "preview" in result
        assert "src/storage/db.ts" in result["affected_files"]
        assert target_file.read_text() == original_target
        assert (temp_typescript_project / "src" / "db.ts").exists()

    def test_move_module_preserves_js_suffix_in_nodenext(
        self, typescript_backend, temp_typescript_nodenext_project
    ):
        """Existing .js specifiers should survive rewrites in NodeNext projects."""
        result = typescript_backend.move_module(
            source="src/utils.ts",
            target="src/core/utils.ts",
            project_root=str(temp_typescript_nodenext_project),
            dry_run=False,
        )

        assert result["success"]
        main_content = (temp_typescript_nodenext_project / "src" / "main.ts").read_text()
        assert './core/utils.js' in main_content


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
        assert result["dry_run"]
        assert "preview" in result
        # Original should be unchanged
        assert (temp_typescript_project / "src" / "utils.ts").read_text() == original_utils

    def test_move_symbol_rejects_same_file(self, typescript_backend, temp_typescript_project):
        """Moving a symbol within the same file should fail closed."""
        with pytest.raises(Exception, match="identical"):
            typescript_backend.move_symbol(
                source_file="src/utils.ts",
                symbol_name="helperFunc",
                target_file="src/utils.ts",
                project_root=str(temp_typescript_project),
                dry_run=False,
            )

    def test_move_symbol_rejects_target_binding_collision(
        self, typescript_backend, temp_typescript_project
    ):
        """Existing target bindings should block a move."""
        (temp_typescript_project / "src" / "helpers.ts").write_text(
            "export const helperFunc = 1;\n"
        )

        with pytest.raises(Exception, match="already has a binding"):
            typescript_backend.move_symbol(
                source_file="src/utils.ts",
                symbol_name="helperFunc",
                target_file="src/helpers.ts",
                project_root=str(temp_typescript_project),
                dry_run=False,
            )

    def test_move_symbol_rejects_unexported_dependencies(
        self, typescript_backend, tmp_path
    ):
        """Source-local dependencies should trigger a fail-closed error."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "source.ts").write_text(
            "const secret = 1;\n"
            "export function helper(): number {\n"
            "    return secret;\n"
            "}\n"
        )
        (project / "src" / "target.ts").write_text("")

        with pytest.raises(Exception, match="exported first: secret"):
            typescript_backend.move_symbol(
                source_file="src/source.ts",
                symbol_name="helper",
                target_file="src/target.ts",
                project_root=str(project),
                dry_run=False,
            )

    def test_move_symbol_rejects_multi_declarator_variable(
        self, typescript_backend, tmp_path
    ):
        """One declarator out of a multi-declarator statement should fail closed."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "source.ts").write_text("export const a = 1, b = 2;\n")
        (project / "src" / "target.ts").write_text("")

        with pytest.raises(Exception, match="multi-declarator"):
            typescript_backend.move_symbol(
                source_file="src/source.ts",
                symbol_name="a",
                target_file="src/target.ts",
                project_root=str(project),
                dry_run=False,
            )

    def test_move_symbol_allows_type_contextual_identifier(
        self, typescript_backend, tmp_path
    ):
        """TypeScript contextual keywords such as type can be bindings."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "source.ts").write_text("export const type = 1;\n")
        (project / "src" / "target.ts").write_text("")

        result = typescript_backend.move_symbol(
            source_file="src/source.ts",
            symbol_name="type",
            target_file="src/target.ts",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        assert "const type = 1" in (project / "src" / "target.ts").read_text()


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

        result = typescript_backend.rename_symbol(
            file="src/utils.ts",
            old_name="helperFunc",
            new_name="assistFunc",
            project_root=str(temp_typescript_project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert "preview" in result
        assert (temp_typescript_project / "src" / "utils.ts").read_text() == original

    def test_rename_updates_callers_without_tsconfig(
        self, typescript_backend, temp_typescript_no_tsconfig_project
    ):
        """rename_symbol should operate on the full project graph without tsconfig."""
        result = typescript_backend.rename_symbol(
            file="src/utils.ts",
            old_name="helperFunc",
            new_name="assistFunc",
            project_root=str(temp_typescript_no_tsconfig_project),
            dry_run=False,
        )

        assert result["success"]
        main_content = (temp_typescript_no_tsconfig_project / "src" / "main.ts").read_text()
        assert "assistFunc" in main_content
        assert "helperFunc" not in main_content

    def test_rename_parameter_requires_selector_when_ambiguous(
        self, typescript_backend, tmp_path
    ):
        """Duplicate parameter names should require a selector."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "params.ts").write_text(
            "export function greet(name: string) { return name; }\n"
            "export function other(name: string) { return name; }\n"
        )

        with pytest.raises(Exception, match="ambiguous"):
            typescript_backend.rename_symbol(
                file="src/params.ts",
                old_name="name",
                new_name="person",
                project_root=str(project),
                dry_run=False,
            )

    def test_rename_parameter_with_selector(self, typescript_backend, tmp_path):
        """Selectors should target one parameter declaration precisely."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "params.ts").write_text(
            "export function greet(name: string) { return name; }\n"
            "export function other(name: string) { return name; }\n"
        )

        result = typescript_backend.rename_symbol(
            file="src/params.ts",
            old_name="name",
            new_name="person",
            project_root=str(project),
            dry_run=False,
            line=1,
            column=23,
        )

        assert result["success"]
        content = (project / "src" / "params.ts").read_text()
        assert "greet(person: string)" in content
        assert "return person;" in content
        assert "other(name: string)" in content

    def test_rename_to_type_contextual_identifier(self, typescript_backend, tmp_path):
        """TypeScript contextual keywords should be valid rename targets."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"commonjs","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "values.ts").write_text(
            "export const value = 1;\n"
            "export const output = value;\n"
        )

        result = typescript_backend.rename_symbol(
            file="src/values.ts",
            old_name="value",
            new_name="type",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = (project / "src" / "values.ts").read_text()
        assert "const type = 1" in content
        assert "output = type" in content


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
        assert all("code" in error for error in errors)


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


class TestDependencyProbe:
    """The backend should surface an actionable error when ts-morph is missing."""

    def test_backend_reports_missing_ts_morph(self, tmp_path, monkeypatch):
        """Simulate missing ts-morph by pointing the marker at a nonexistent path."""
        from backends import typescript as ts_module

        monkeypatch.setattr(
            ts_module,
            "TSMORPH_MODULE_MARKER",
            tmp_path / "never_exists" / "ts-morph" / "package.json",
        )

        backend = ts_module.TypeScriptBackend()
        with pytest.raises(RuntimeError) as info:
            backend.move_module(
                source="src/a.ts",
                target="src/b.ts",
                project_root=str(tmp_path),
                dry_run=True,
            )

        message = str(info.value)
        assert "ts-morph is not installed" in message
        assert "pnpm install" in message

    def test_backend_reports_missing_script(self, tmp_path, monkeypatch):
        """Missing refactor.js should give a clean, actionable error too."""
        from backends import typescript as ts_module

        monkeypatch.setattr(
            ts_module,
            "TSMORPH_SCRIPT",
            tmp_path / "never_exists" / "refactor.js",
        )

        backend = ts_module.TypeScriptBackend()
        with pytest.raises(RuntimeError, match="ts-morph script not found"):
            backend.move_module(
                source="src/a.ts",
                target="src/b.ts",
                project_root=str(tmp_path),
                dry_run=True,
            )

    def test_probe_runs_only_once_per_instance(self, tmp_path, monkeypatch):
        """The dependency check should be cached on the instance, not re-stat per call."""
        from backends import typescript as ts_module

        monkeypatch.setattr(
            ts_module,
            "TSMORPH_MODULE_MARKER",
            tmp_path / "never_exists" / "ts-morph" / "package.json",
        )

        backend = ts_module.TypeScriptBackend()
        call_count = {"n": 0}
        original = ts_module.TypeScriptBackend._check_dependencies

        def counting_check() -> str | None:
            call_count["n"] += 1
            return original()

        monkeypatch.setattr(
            ts_module.TypeScriptBackend, "_check_dependencies", staticmethod(counting_check)
        )

        for _ in range(3):
            with pytest.raises(RuntimeError):
                backend.move_module(
                    source="src/a.ts",
                    target="src/b.ts",
                    project_root=str(tmp_path),
                    dry_run=True,
                )

        assert call_count["n"] == 0  # cached result reused; probe not re-invoked
