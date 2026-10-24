from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath


class ScopeViolation(RuntimeError):
    pass


def scope_files_for_state(state: dict) -> list[str]:
    files = [str(path) for path in state.get("scope_files") or [] if str(path).strip()]
    if not files:
        files = [
            str(path)
            for path in state.get("suspected_files") or []
            if str(path).strip() and not str(path).startswith("tests/")
        ]
    return files


def audit_patch_scope(
    edits: list,
    root: Path,
    allowed_files: list[str],
    allowed_symbols: list[str],
) -> list[str]:
    allowed = {_normalize_path(path) for path in allowed_files}
    allowed.discard(None)
    if not allowed:
        return ["scope has no allowed files"]

    symbols = {symbol for symbol in allowed_symbols if symbol}
    violations: list[str] = []
    for edit in edits:
        path = _normalize_path(_edit_value(edit, "path"))
        if path is None:
            violations.append("edit path is not repository-relative")
            continue
        if path.startswith("tests/") or path == "tests":
            violations.append(f"{path}: test files are not editable")
            continue
        if path not in allowed:
            violations.append(f"{path}: outside issue scope {sorted(allowed)}")
            continue

        if symbols:
            old_text = str(_edit_value(edit, "old_text") or "")
            changed = _enclosing_symbols(root / path, old_text)
            if changed and not any(_symbol_allowed(symbol, symbols) for symbol in changed):
                violations.append(
                    f"{path}: changes {sorted(changed)}, allowed symbols are {sorted(symbols)}"
                )
    return violations


def _edit_value(edit, key: str):
    if isinstance(edit, dict):
        return edit.get(key)
    return getattr(edit, key, None)


def _normalize_path(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        return None
    if raw.startswith("./"):
        raw = raw[2:]
    pure = PurePosixPath(raw)
    if ".." in pure.parts:
        return None
    return str(pure)


def _enclosing_symbols(file_path: Path, old_text: str) -> set[str]:
    if file_path.suffix != ".py" or not old_text:
        return set()
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    offset = source.find(old_text)
    if offset < 0:
        return set()
    line = source.count("\n", 0, offset) + 1
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    candidates: list[tuple[int, set[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end_line = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= line <= end_line:
            names = {node.name}
            names.update(parent for parent in _parents(tree, node))
            candidates.append((end_line - node.lineno, names))

    if not candidates:
        return set()
    return min(candidates, key=lambda item: item[0])[1]


def _parents(tree: ast.AST, target: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if target in ast.walk(node) and node is not target:
            names.add(node.name)
    return names


def _symbol_allowed(symbol: str, allowed: set[str]) -> bool:
    if symbol in allowed:
        return True
    for value in allowed:
        bare = value.split(":")[-1].split(".")[-1]
        if bare == symbol:
            return True
    return False
