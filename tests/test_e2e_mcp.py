"""End-to-end MCP tests over stdio against the real server subprocess."""
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = REPO_ROOT / "server"

pytestmark = pytest.mark.asyncio


def _server_env() -> dict[str, str]:
    """Build the subprocess environment for the MCP server."""
    pythonpath_parts = [str(SERVER_ROOT)]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        pythonpath_parts.append(existing)

    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(pythonpath_parts),
    }


@asynccontextmanager
async def open_mcp_session():
    """Open a real MCP stdio session against server/main.py."""
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_ROOT / "main.py")],
        cwd=str(SERVER_ROOT),
        env=_server_env(),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def call_json(session: ClientSession, name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Call a tool and decode the single JSON text payload."""
    result = await session.call_tool(name, arguments)
    assert not result.isError
    assert len(result.content) == 1
    payload = result.content[0]
    assert payload.type == "text"
    return json.loads(payload.text)


def snapshot_tree(root: Path) -> dict[str, str]:
    """Capture a text snapshot of all files under a fixture tree."""
    snapshot: dict[str, str] = {}
    for file_path in sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and ".ropeproject" not in path.parts
    ):
        snapshot[str(file_path.relative_to(root))] = file_path.read_text()
    return snapshot


class TestMcpE2E:
    """End-to-end MCP coverage against the real subprocess server."""

    async def test_list_tools_over_stdio(self):
        """The subprocess server should expose the expected tool surface."""
        async with open_mcp_session() as session:
            result = await session.list_tools()

        tools = {tool.name: tool for tool in result.tools}
        assert set(tools) == {
            "move_module",
            "move_symbol",
            "rename_symbol",
            "validate_imports",
            "organize_imports",
            "extract_variable",
            "extract_function",
            "inline_symbol",
        }
        assert "overwrite" in tools["move_module"].inputSchema["properties"]
        assert "apply" in tools["move_module"].inputSchema["properties"]
        assert "dry_run" not in tools["move_module"].inputSchema["properties"]
        assert "line" in tools["rename_symbol"].inputSchema["properties"]
        assert "column" in tools["rename_symbol"].inputSchema["properties"]

    async def test_python_move_module_preview_over_stdio(self, temp_python_project):
        """Default preview module moves should round-trip without mutating files."""
        before = snapshot_tree(temp_python_project)

        async with open_mcp_session() as session:
            data = await call_json(session, "move_module", {
                "source": "src/db.py",
                "target": "src/storage/db.py",
                "project_root": str(temp_python_project),
            })

        assert data["success"]
        assert data["apply"] is False
        assert "apply: true" in data["message"]
        assert "dry_run" not in data
        assert "src/db.py" in data["affected_files"]
        assert "src/main.py" in data["affected_files"]
        assert "preview" in data
        assert snapshot_tree(temp_python_project) == before

    async def test_python_parameter_rename_with_selector_over_stdio(self, tmp_path):
        """Selector-based parameter renames should work end to end through MCP."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "params.py").write_text(
            "def greet(name):\n"
            "    return name\n\n"
            "def other(name):\n"
            "    return name\n"
        )

        async with open_mcp_session() as session:
            data = await call_json(session, "rename_symbol", {
                "file": "params.py",
                "old_name": "name",
                "new_name": "person",
                "project_root": str(project),
                "apply": True,
                "line": 1,
                "column": 11,
            })

        assert data["success"]
        content = (project / "params.py").read_text()
        assert "def greet(person):" in content
        assert "return person" in content
        assert "def other(name):" in content

    async def test_python_chained_refactor_workflow_over_stdio(self, tmp_path):
        """A multi-step Python refactor should stay coherent within one MCP session."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "__init__.py").write_text("")
        (project / "src" / "utils.py").write_text(
            "def helper_func(name):\n"
            "    return name\n"
        )
        (project / "src" / "main.py").write_text(
            "from src.utils import helper_func\n\n"
            "def run(name):\n"
            "    return helper_func(name)\n"
        )

        async with open_mcp_session() as session:
            move_data = await call_json(session, "move_module", {
                "source": "src/utils.py",
                "target": "src/core/utils.py",
                "project_root": str(project),
                "apply": True,
            })
            rename_data = await call_json(session, "rename_symbol", {
                "file": "src/core/utils.py",
                "old_name": "name",
                "new_name": "person",
                "project_root": str(project),
                "apply": True,
                "line": 1,
                "column": 17,
            })
            validate_data = await call_json(session, "validate_imports", {
                "project_root": str(project),
                "language": "python",
            })

        assert move_data["success"]
        assert rename_data["success"]
        assert validate_data["valid"]
        main_content = (project / "src" / "main.py").read_text()
        moved_content = (project / "src" / "core" / "utils.py").read_text()
        assert "from src.core.utils import helper_func" in main_content
        assert "def helper_func(person):" in moved_content

    async def test_typescript_move_module_preserves_js_suffix_over_stdio(
        self,
        temp_typescript_nodenext_project,
    ):
        """NodeNext import style should survive a real MCP module move."""
        async with open_mcp_session() as session:
            data = await call_json(session, "move_module", {
                "source": "src/utils.ts",
                "target": "src/core/utils.ts",
                "project_root": str(temp_typescript_nodenext_project),
                "apply": True,
            })

        assert data["success"]
        assert "src/main.ts" in data["affected_files"]
        main_content = (temp_typescript_nodenext_project / "src" / "main.ts").read_text()
        assert './core/utils.js' in main_content

    async def test_typescript_chained_refactor_workflow_over_stdio(self, tmp_path):
        """A multi-step TypeScript refactor should preserve import style and validation."""
        project = tmp_path / "tsproject"
        project.mkdir()
        (project / "tsconfig.json").write_text(
            '{"compilerOptions":{"target":"ES2020","module":"NodeNext","strict":true},"include":["src/**/*"]}'
        )
        (project / "src").mkdir()
        (project / "src" / "utils.ts").write_text(
            "export function helperFunc(value: string): string {\n"
            "    return value;\n"
            "}\n"
        )
        (project / "src" / "main.ts").write_text(
            'import { helperFunc } from "./utils.js";\n'
            'export const value = helperFunc("x");\n'
        )

        async with open_mcp_session() as session:
            move_data = await call_json(session, "move_module", {
                "source": "src/utils.ts",
                "target": "src/core/utils.ts",
                "project_root": str(project),
                "apply": True,
            })
            rename_data = await call_json(session, "rename_symbol", {
                "file": "src/core/utils.ts",
                "old_name": "helperFunc",
                "new_name": "assistFunc",
                "project_root": str(project),
                "apply": True,
            })
            validate_data = await call_json(session, "validate_imports", {
                "project_root": str(project),
                "language": "typescript",
            })

        assert move_data["success"]
        assert rename_data["success"]
        assert validate_data["valid"]
        main_content = (project / "src" / "main.ts").read_text()
        assert 'import { assistFunc } from "./core/utils.js";' in main_content
        assert 'assistFunc("x")' in main_content

    async def test_typescript_move_symbol_fail_closed_over_stdio(self, tmp_path):
        """Unsafe symbol moves should return an error payload and leave files untouched."""
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
        before = snapshot_tree(project)

        async with open_mcp_session() as session:
            data = await call_json(session, "move_symbol", {
                "source_file": "src/source.ts",
                "symbol_name": "helper",
                "target_file": "src/target.ts",
                "project_root": str(project),
            })

        assert "error" in data
        assert "exported first: secret" in data["error"]
        assert snapshot_tree(project) == before

    async def test_server_recovers_after_error_over_stdio(self, tmp_path):
        """A tool error should not poison the live MCP session."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "__init__.py").write_text("")
        (project / "src" / "db.py").write_text(
            "class Database:\n"
            "    pass\n"
        )

        async with open_mcp_session() as session:
            error_data = await call_json(session, "move_module", {
                "source": "../outside.py",
                "target": "src/inside.py",
                "project_root": str(project),
            })
            validate_data = await call_json(session, "validate_imports", {
                "project_root": str(project),
                "language": "python",
            })

        assert "error" in error_data
        assert "escapes project root" in error_data["error"]
        assert validate_data["valid"]
