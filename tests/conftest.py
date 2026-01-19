"""Shared fixtures for hermetic refactory tests."""
import shutil
import sys
from pathlib import Path

import pytest

# Add server to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))


@pytest.fixture
def temp_python_project(tmp_path):
    """Create a hermetic Python project for testing."""
    project = tmp_path / "project"
    project.mkdir()

    # Create package structure
    (project / "src").mkdir()
    (project / "src" / "__init__.py").write_text("")

    # Main module that imports from utils
    (project / "src" / "main.py").write_text('''"""Main module."""
from src.utils import helper_func, HelperClass
from src.db import Database

def run():
    """Run the app."""
    helper_func()
    obj = HelperClass()
    db = Database()
    return obj, db
''')

    # Utils module with functions and classes
    (project / "src" / "utils.py").write_text('''"""Utility functions."""

def helper_func():
    """A helper function."""
    return "helped"

def other_func():
    """Another function."""
    return helper_func()

class HelperClass:
    """A helper class."""

    def method(self):
        """Class method."""
        return helper_func()
''')

    # Database module
    (project / "src" / "db.py").write_text('''"""Database module."""
from src.utils import helper_func

class Database:
    """Database class."""

    def connect(self):
        """Connect to database."""
        helper_func()
        return True
''')

    # Tests that import from src
    (project / "tests").mkdir()
    (project / "tests" / "__init__.py").write_text("")
    (project / "tests" / "test_utils.py").write_text('''"""Test utils."""
from src.utils import helper_func, HelperClass

def test_helper():
    assert helper_func() == "helped"

def test_class():
    obj = HelperClass()
    assert obj.method() == "helped"
''')

    yield project

    # Cleanup
    shutil.rmtree(project, ignore_errors=True)


@pytest.fixture
def temp_typescript_project(tmp_path):
    """Create a hermetic TypeScript project for testing."""
    project = tmp_path / "tsproject"
    project.mkdir()

    # Create tsconfig.json
    (project / "tsconfig.json").write_text('''{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "strict": true,
    "esModuleInterop": true,
    "outDir": "./dist",
    "rootDir": "./src"
  },
  "include": ["src/**/*"]
}
''')

    # Create source structure
    (project / "src").mkdir()

    # Main module
    (project / "src" / "main.ts").write_text('''import { helperFunc, HelperClass } from "./utils";
import { Database } from "./db";

export function run(): void {
    helperFunc();
    const obj = new HelperClass();
    const db = new Database();
    console.log(obj, db);
}
''')

    # Utils module
    (project / "src" / "utils.ts").write_text('''export function helperFunc(): string {
    return "helped";
}

export function otherFunc(): string {
    return helperFunc();
}

export class HelperClass {
    method(): string {
        return helperFunc();
    }
}
''')

    # Database module
    (project / "src" / "db.ts").write_text('''import { helperFunc } from "./utils";

export class Database {
    connect(): boolean {
        helperFunc();
        return true;
    }
}
''')

    yield project

    # Cleanup
    shutil.rmtree(project, ignore_errors=True)


@pytest.fixture
def python_backend():
    """Get Python refactoring backend."""
    from backends.python import PythonBackend
    return PythonBackend()


@pytest.fixture
def typescript_backend():
    """Get TypeScript refactoring backend."""
    from backends.typescript import TypeScriptBackend
    return TypeScriptBackend()
