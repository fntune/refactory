"""Hermetic tests for MCP server integration."""
import json
import sys
from pathlib import Path

import pytest

# Add server to path
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))


class TestToolListing:
    """Tests for MCP tool listing."""

    @pytest.mark.asyncio
    async def test_lists_all_tools(self):
        """Server should list all expected tools."""
        from main import list_tools

        tools = await list_tools()
        tool_names = {t.name for t in tools}

        assert "move_module" in tool_names
        assert "move_symbol" in tool_names
        assert "rename_symbol" in tool_names
        assert "validate_imports" in tool_names
        assert "organize_imports" in tool_names
        assert "extract_variable" in tool_names
        assert "extract_function" in tool_names
        assert "inline_symbol" in tool_names

    @pytest.mark.asyncio
    async def test_tool_schemas_valid(self):
        """All tools should have valid JSON schemas."""
        from main import list_tools

        tools = await list_tools()

        for tool in tools:
            assert tool.inputSchema is not None
            assert tool.inputSchema.get("type") == "object"
            assert "properties" in tool.inputSchema
            if "project_root" in tool.inputSchema["properties"]:
                assert "project_root" in tool.inputSchema.get("required", [])
            assert "expected_git_root" not in tool.inputSchema["properties"]

    @pytest.mark.asyncio
    async def test_move_module_schema(self):
        """move_module should have correct schema."""
        from main import list_tools

        tools = await list_tools()
        move_module = next(t for t in tools if t.name == "move_module")

        props = move_module.inputSchema["properties"]
        assert "source" in props
        assert "target" in props
        assert "project_root" in props
        assert "expected_git_root" not in props
        assert "apply" in props
        assert "dry_run" not in props
        assert "overwrite" in props

        required = move_module.inputSchema["required"]
        assert "source" in required
        assert "target" in required
        assert "project_root" in required

    @pytest.mark.asyncio
    async def test_rename_symbol_schema(self):
        """rename_symbol should expose selector fields."""
        from main import list_tools

        tools = await list_tools()
        rename_symbol = next(t for t in tools if t.name == "rename_symbol")

        props = rename_symbol.inputSchema["properties"]
        assert "line" in props
        assert "column" in props


class TestToolExecution:
    """Tests for MCP tool execution."""

    @pytest.mark.asyncio
    async def test_move_module_execution(self, temp_python_project):
        """move_module tool should execute correctly."""
        from main import call_tool

        result = await call_tool("move_module", {
            "source": "src/db.py",
            "target": "src/storage/db.py",
            "project_root": str(temp_python_project),
        })

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["success"]
        assert data["apply"] is False
        assert "apply: true" in data["message"]
        assert "dry_run" not in data

    @pytest.mark.asyncio
    async def test_rename_symbol_execution(self, temp_python_project):
        """rename_symbol tool should execute correctly."""
        from main import call_tool

        result = await call_tool("rename_symbol", {
            "file": "src/utils.py",
            "old_name": "helper_func",
            "new_name": "assist_func",
            "project_root": str(temp_python_project),
        })

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["success"]
        assert data["apply"] is False

    @pytest.mark.asyncio
    async def test_rename_parameter_execution_with_selector(self, tmp_path):
        """rename_symbol should thread selector-based parameter renames."""
        from main import call_tool

        project = tmp_path / "project"
        project.mkdir()
        (project / "params.py").write_text(
            "def greet(name):\n"
            "    return name\n\n"
            "def other(name):\n"
            "    return name\n"
        )

        result = await call_tool("rename_symbol", {
            "file": "params.py",
            "old_name": "name",
            "new_name": "person",
            "project_root": str(project),
            "apply": True,
            "line": 1,
            "column": 11,
        })

        data = json.loads(result[0].text)
        assert data["success"]
        assert data["apply"] is True
        content = (project / "params.py").read_text()
        assert "def greet(person):" in content
        assert "def other(name):" in content

    @pytest.mark.asyncio
    async def test_validate_imports_execution(self, temp_python_project):
        """validate_imports tool should execute correctly."""
        from main import call_tool

        result = await call_tool("validate_imports", {
            "project_root": str(temp_python_project),
            "language": "python",
        })

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert "valid" in data
        assert data["valid"]  # Project should have no errors

    @pytest.mark.asyncio
    async def test_validate_imports_reports_errors(self, temp_python_project):
        """validate_imports should surface Python import failures."""
        from main import call_tool

        (temp_python_project / "src" / "broken_import.py").write_text(
            "import src.missing_module\n"
        )

        result = await call_tool("validate_imports", {
            "project_root": str(temp_python_project),
            "language": "python",
        })

        data = json.loads(result[0].text)
        assert not data["valid"]
        assert data["issues"][0]["language"] == "python"
        assert any(
            issue.get("file") == "src/broken_import.py"
            for issue in data["issues"][0]["errors"]
        )

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, temp_python_project):
        """Unknown tool should return error."""
        from main import call_tool

        result = await call_tool("unknown_tool", {})

        data = json.loads(result[0].text)
        assert "error" in data


class TestLanguageDetection:
    """Tests for automatic language detection."""

    def test_detects_python(self):
        """Should detect Python from .py extension."""
        from main import detect_language

        assert detect_language("src/module.py") == "python"
        assert detect_language("tests/test_foo.py") == "python"

    def test_detects_typescript(self):
        """Should detect TypeScript from extensions."""
        from main import detect_language

        assert detect_language("src/module.ts") == "typescript"
        assert detect_language("src/component.tsx") == "typescript"
        assert detect_language("src/utils.js") == "typescript"
        assert detect_language("src/app.jsx") == "typescript"

    def test_unsupported_extension_raises(self):
        """Should raise for unsupported extensions."""
        from main import detect_language

        with pytest.raises(ValueError):
            detect_language("src/module.rb")

        with pytest.raises(ValueError):
            detect_language("src/module.go")


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_missing_required_param_handled(self, temp_python_project):
        """Missing required params should return error."""
        from main import call_tool

        # Missing 'target' param
        result = await call_tool("move_module", {
            "source": "src/db.py",
            "project_root": str(temp_python_project),
        })

        data = json.loads(result[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_missing_project_root_handled(self):
        """All tool calls must provide an explicit project_root."""
        from main import call_tool

        result = await call_tool("move_module", {
            "source": "src/db.py",
            "target": "src/storage/db.py",
        })

        data = json.loads(result[0].text)
        assert "error" in data
        assert "project_root is required" in data["error"]

    @pytest.mark.asyncio
    async def test_relative_project_root_rejected(self):
        """project_root must be absolute so cwd drift cannot pick a checkout."""
        from main import call_tool

        result = await call_tool("validate_imports", {
            "project_root": ".",
            "language": "python",
        })

        data = json.loads(result[0].text)
        assert "error" in data
        assert "project_root must be an absolute path" in data["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_file_handled(self, temp_python_project):
        """Nonexistent file should return error, not crash."""
        from main import call_tool

        result = await call_tool("move_module", {
            "source": "src/nonexistent.py",
            "target": "src/somewhere.py",
            "project_root": str(temp_python_project),
        })

        data = json.loads(result[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_invalid_project_root_handled(self, tmp_path):
        """Invalid project root should return error."""
        from main import call_tool

        result = await call_tool("validate_imports", {
            "project_root": str(tmp_path / "nonexistent"),
        })

        # Should return result (possibly empty) not crash
        assert len(result) == 1


class TestApplyMode:
    """Tests for preview/apply MCP semantics."""

    @pytest.mark.asyncio
    async def test_apply_defaults_to_preview(self, temp_python_project):
        """Omitting apply should preview without mutating files."""
        from main import call_tool

        result = await call_tool("move_module", {
            "source": "src/db.py",
            "target": "src/storage/db.py",
            "project_root": str(temp_python_project),
        })

        data = json.loads(result[0].text)
        assert data["apply"] is False
        assert "apply: true" in data["message"]
        assert "preview" in data

        # File should not have moved
        assert (temp_python_project / "src" / "db.py").exists()
        assert not (temp_python_project / "src" / "storage").exists()

    @pytest.mark.asyncio
    async def test_preview_reports_changes(self, temp_python_project):
        """Preview should report what would change."""
        from main import call_tool

        result = await call_tool("rename_symbol", {
            "file": "src/utils.py",
            "old_name": "helper_func",
            "new_name": "assist_func",
            "project_root": str(temp_python_project),
        })

        data = json.loads(result[0].text)
        assert data["apply"] is False
        assert "affected_files" in data
        assert len(data["affected_files"]) > 0
        assert "preview" in data

    @pytest.mark.asyncio
    async def test_apply_must_be_literal_true(self, temp_python_project):
        """Only JSON boolean true should apply; truthy strings still preview."""
        from main import call_tool

        result = await call_tool("move_module", {
            "source": "src/db.py",
            "target": "src/storage/db.py",
            "project_root": str(temp_python_project),
            "apply": "true",
        })

        data = json.loads(result[0].text)
        assert data["success"]
        assert data["apply"] is False
        assert (temp_python_project / "src" / "db.py").exists()
        assert not (temp_python_project / "src" / "storage").exists()
