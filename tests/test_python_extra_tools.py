"""Tests for Python-only Rope tools."""
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "server"))


class TestPythonOnlyToolListing:
    @pytest.mark.asyncio
    async def test_lists_python_only_tools(self):
        from main import list_tools

        tools = await list_tools()
        tool_names = {tool.name for tool in tools}

        assert "organize_imports" in tool_names
        assert "extract_variable" in tool_names
        assert "extract_function" in tool_names
        assert "inline_symbol" in tool_names


class TestPythonOnlyBackendTools:
    def test_organize_imports_apply(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "mod.py").write_text(
            "import os\n"
            "import sys\n\n"
            "print(os.getcwd())\n"
        )

        result = python_backend.organize_imports(
            file="mod.py",
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = (project / "mod.py").read_text()
        assert "import os" in content
        assert "import sys" not in content

    def test_organize_imports_dry_run_returns_preview(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "mod.py").write_text(
            "import os\n"
            "import sys\n\n"
            "print(os.getcwd())\n"
        )

        original = (project / "mod.py").read_text()
        result = python_backend.organize_imports(
            file="mod.py",
            project_root=str(project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert "preview" in result
        assert (project / "mod.py").read_text() == original

    def test_extract_variable_apply(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "mod.py").write_text(
            "def run():\n"
            "    value = 1 + 2\n"
            "    return value\n"
        )

        result = python_backend.extract_variable(
            file="mod.py",
            new_name="answer",
            start_line=2,
            start_column=13,
            end_line=2,
            end_column=18,
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = (project / "mod.py").read_text()
        assert "answer = 1 + 2" in content
        assert "value = answer" in content

    def test_extract_variable_dry_run_returns_preview(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "def run():\n"
            "    value = 1 + 2\n"
            "    return value\n"
        )

        original = mod.read_text()
        result = python_backend.extract_variable(
            file="mod.py",
            new_name="answer",
            start_line=2,
            start_column=13,
            end_line=2,
            end_column=18,
            project_root=str(project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert result["preview"]
        assert "mod.py" in result["affected_files"]
        assert mod.read_text() == original

    def test_extract_function_apply(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "mod.py").write_text(
            "def run():\n"
            "    total = 1 + 2\n"
            "    print(total)\n"
        )

        result = python_backend.extract_function(
            file="mod.py",
            new_name="compute_total",
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=18,
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = (project / "mod.py").read_text()
        assert "def compute_total():" in content
        assert "total = compute_total()" in content

    def test_extract_function_dry_run_returns_preview(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "def run():\n"
            "    total = 1 + 2\n"
            "    print(total)\n"
        )

        original = mod.read_text()
        result = python_backend.extract_function(
            file="mod.py",
            new_name="compute_total",
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=18,
            project_root=str(project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert result["preview"]
        assert "mod.py" in result["affected_files"]
        assert mod.read_text() == original

    def test_inline_symbol_apply(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "mod.py").write_text(
            "VALUE = 1\n\n"
            "def run():\n"
            "    return VALUE\n"
        )

        result = python_backend.inline_symbol(
            file="mod.py",
            line=4,
            column=12,
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = (project / "mod.py").read_text()
        assert "return 1" in content
        assert "VALUE = 1" not in content

    def test_inline_symbol_dry_run_returns_preview(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "VALUE = 1\n\n"
            "def run():\n"
            "    return VALUE\n"
        )

        original = mod.read_text()
        result = python_backend.inline_symbol(
            file="mod.py",
            line=4,
            column=12,
            project_root=str(project),
            dry_run=True,
        )

        assert result["success"]
        assert result["dry_run"]
        assert result["preview"]
        assert "mod.py" in result["affected_files"]
        assert mod.read_text() == original

    def test_inline_symbol_preserves_operator_precedence(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        mod = project / "calc.py"
        mod.write_text(
            "def calc():\n"
            "    s = 2 + 3\n"
            "    return s * s\n"
        )

        result = python_backend.inline_symbol(
            file="calc.py",
            line=2,
            column=5,
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = mod.read_text()
        namespace: dict[str, Any] = {}
        exec(content, namespace)
        assert namespace["calc"]() == 25, (
            f"inline_symbol corrupted arithmetic; new source:\n{content}"
        )
        assert "s = 2 + 3" not in content

    def test_inline_symbol_does_not_wrap_safe_atom(self, python_backend, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        mod = project / "atom.py"
        mod.write_text(
            "def use():\n"
            "    x = 42\n"
            "    return x + 1\n"
        )

        result = python_backend.inline_symbol(
            file="atom.py",
            line=2,
            column=5,
            project_root=str(project),
            dry_run=False,
        )

        assert result["success"]
        content = mod.read_text()
        assert "return 42 + 1" in content
        assert "(42)" not in content

    def test_inline_symbol_wraps_dry_run_fails_without_mutating_disk(
        self, python_backend, tmp_path,
    ):
        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "def f():\n"
            "    s = 2 + 3\n"
            "    return s * s\n"
        )
        original = mod.read_text()

        with pytest.raises(ValueError, match="inline_symbol dry-run"):
            python_backend.inline_symbol(
                file="mod.py",
                line=2,
                column=5,
                project_root=str(project),
                dry_run=True,
            )

        assert mod.read_text() == original, "dry_run must not mutate disk"

    def test_repeated_dry_runs_remain_repeatable(self, python_backend, temp_python_project):
        first = python_backend.rename_symbol(
            file="src/utils.py",
            old_name="helper_func",
            new_name="assist_func",
            project_root=str(temp_python_project),
            dry_run=True,
        )
        second = python_backend.rename_symbol(
            file="src/utils.py",
            old_name="helper_func",
            new_name="assist_func",
            project_root=str(temp_python_project),
            dry_run=True,
        )

        assert first["success"]
        assert second["success"]
        assert "preview" in first
        assert "preview" in second
        assert "def helper_func" in (temp_python_project / "src" / "utils.py").read_text()


class TestPythonOnlyMcpExecution:
    @pytest.mark.asyncio
    async def test_organize_imports_tool_execution(self, tmp_path):
        from main import call_tool

        project = tmp_path / "project"
        project.mkdir()
        (project / "mod.py").write_text(
            "import os\n"
            "import sys\n\n"
            "print(os.getcwd())\n"
        )

        result = await call_tool("organize_imports", {
            "file": "mod.py",
            "project_root": str(project),
            "dry_run": True,
        })

        data = json.loads(result[0].text)
        assert data["success"]
        assert data["dry_run"]
        assert "preview" in data

    @pytest.mark.asyncio
    async def test_extract_variable_tool_execution(self, tmp_path):
        from main import call_tool

        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "def run():\n"
            "    value = 1 + 2\n"
            "    return value\n"
        )

        original = mod.read_text()
        result = await call_tool("extract_variable", {
            "file": "mod.py",
            "new_name": "answer",
            "start_line": 2,
            "start_column": 13,
            "end_line": 2,
            "end_column": 18,
            "project_root": str(project),
            "dry_run": True,
        })

        data = json.loads(result[0].text)
        assert data["success"]
        assert data["dry_run"]
        assert data["preview"]
        assert mod.read_text() == original

    @pytest.mark.asyncio
    async def test_extract_function_tool_execution(self, tmp_path):
        from main import call_tool

        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "def run():\n"
            "    total = 1 + 2\n"
            "    print(total)\n"
        )

        original = mod.read_text()
        result = await call_tool("extract_function", {
            "file": "mod.py",
            "new_name": "compute_total",
            "start_line": 2,
            "start_column": 5,
            "end_line": 2,
            "end_column": 18,
            "project_root": str(project),
            "dry_run": True,
        })

        data = json.loads(result[0].text)
        assert data["success"]
        assert data["dry_run"]
        assert data["preview"]
        assert mod.read_text() == original

    @pytest.mark.asyncio
    async def test_inline_symbol_tool_execution(self, tmp_path):
        from main import call_tool

        project = tmp_path / "project"
        project.mkdir()
        mod = project / "mod.py"
        mod.write_text(
            "VALUE = 1\n\n"
            "def run():\n"
            "    return VALUE\n"
        )

        original = mod.read_text()
        result = await call_tool("inline_symbol", {
            "file": "mod.py",
            "line": 4,
            "column": 12,
            "project_root": str(project),
            "dry_run": True,
        })

        data = json.loads(result[0].text)
        assert data["success"]
        assert data["dry_run"]
        assert data["preview"]
        assert mod.read_text() == original
