"""Load an uploaded strategy file: detect its language and validate it before any data is fetched."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .pine.compiler import compile_pine
from .pine.runtime import PineError

PYTHON_ENTRY_POINTS = ("generate_signals", "strategy", "signals")
MAX_FILE_BYTES = 512 * 1024


class StrategyError(ValueError):
    """The uploaded file can't be used. `line` points at the problem when known."""

    def __init__(self, message: str, line: int | None = None):
        self.message, self.line = message, line
        super().__init__(f"Line {line}: {message}" if line else message)


@dataclass
class StrategySpec:
    kind: str           # "python" | "pine"
    language: str       # human label, e.g. "Pine Script v5"
    filename: str
    name: str
    source: str
    entry_point: str | None = None


def detect_kind(filename: str, source: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".py":
        return "python"
    if ext in (".pine", ".pinescript", ".pine5", ".ps"):
        return "pine"
    text = source.lstrip()
    pine_hints = ("//@version", "strategy(", "indicator(", "study(")
    if text.startswith("//") or any(h in source for h in pine_hints[:1]):
        return "pine"
    if "def " in source or "import " in source:
        return "python"
    if any(h in source for h in pine_hints):
        return "pine"
    raise StrategyError("Couldn't tell whether this file is Python or Pine Script. "
                        "Use a .py or .pine file extension.")


def load_strategy(filename: str, data: bytes) -> StrategySpec:
    if len(data) > MAX_FILE_BYTES:
        raise StrategyError("The strategy file is larger than 512 KB.")
    try:
        source = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise StrategyError("The strategy file must be UTF-8 text.") from None
    if not source.strip():
        raise StrategyError("The strategy file is empty.")
    kind = detect_kind(filename, source)
    return _load_python(filename, source) if kind == "python" else _load_pine(filename, source)


def _load_python(filename: str, source: str) -> StrategySpec:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise StrategyError(f"Python syntax error: {exc.msg}", exc.lineno) from None
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    entry = next((f for f in PYTHON_ENTRY_POINTS if f in funcs), None)
    if entry is None:
        raise StrategyError("The Python file needs a top-level function `generate_signals(data)` that returns "
                            "the signals (see the example strategy).")
    name = Path(filename).stem.replace("_", " ").strip() or "Python strategy"
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAME" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                name = node.value.value
    return StrategySpec("python", "Python", filename, name, source, entry)


def _load_pine(filename: str, source: str) -> StrategySpec:
    try:
        compiled = compile_pine(source)
    except PineError as exc:
        raise StrategyError(exc.message, exc.line) from None
    return StrategySpec("pine", f"Pine Script v{compiled.version}", filename, compiled.declaration.title, source)
