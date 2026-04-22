#!/bin/bash
# Run refactory tests
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Install test dependencies if needed
if ! python -c "import pytest" 2>/dev/null; then
    echo "Installing test dependencies..."
    pip install pytest pytest-asyncio
fi

# Install project dependencies if needed
if ! python -c "import rope" 2>/dev/null; then
    echo "Installing rope..."
    pip install rope
fi

if ! python -c "import mcp" 2>/dev/null; then
    echo "Installing mcp..."
    pip install mcp
fi

# Run tests
echo "Running tests..."
PYTHONPATH="$PROJECT_ROOT/server:$PYTHONPATH" pytest tests/ -v "$@"
