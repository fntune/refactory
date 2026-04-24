"""Shared validation helpers for refactory backends."""
import keyword
import re

TS_IDENTIFIER_RE = re.compile(r"^[$A-Za-z_][$0-9A-Za-z_]*$")
TS_RESERVED_WORDS = {
    "await",
    "break",
    "case",
    "catch",
    "class",
    "const",
    "continue",
    "debugger",
    "default",
    "delete",
    "do",
    "else",
    "enum",
    "export",
    "extends",
    "false",
    "finally",
    "for",
    "function",
    "if",
    "implements",
    "import",
    "in",
    "instanceof",
    "interface",
    "let",
    "new",
    "null",
    "package",
    "private",
    "protected",
    "public",
    "return",
    "static",
    "super",
    "switch",
    "this",
    "throw",
    "true",
    "try",
    "typeof",
    "var",
    "void",
    "while",
    "with",
    "yield",
}


def validate_identifier(name: str, language: str) -> None:
    """Validate a symbol identifier for the requested language."""
    if language == "python":
        if not name.isidentifier():
            raise ValueError(f"Invalid Python identifier: {name!r}")
        if keyword.iskeyword(name):
            raise ValueError(f"Invalid Python identifier: {name!r}")
        return

    if language == "typescript":
        if not TS_IDENTIFIER_RE.match(name):
            raise ValueError(f"Invalid TypeScript identifier: {name!r}")
        if name in TS_RESERVED_WORDS:
            raise ValueError(f"Invalid TypeScript identifier: {name!r}")
        return

    raise ValueError(f"Unsupported language for identifier validation: {language}")


def validate_position_selector(line: int | None, column: int | None) -> tuple[int | None, int | None]:
    """Validate and normalize optional 1-based source selectors."""
    if line is None and column is None:
        return None, None
    if line is None:
        raise ValueError("column requires line")
    if line < 1:
        raise ValueError("line must be 1-based")
    if column is not None and column < 1:
        raise ValueError("column must be 1-based")
    return line, column
