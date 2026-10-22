from __future__ import annotations

import re
from dataclasses import dataclass, field

import tree_sitter_python
from tree_sitter import Language, Node, Parser

_LANGUAGE = Language(tree_sitter_python.language())
_PARSER = Parser(_LANGUAGE)
_IMPORT_FROM = re.compile(
    r"from\s+(?P<dots>\.*)(?P<module>[A-Za-z_][\w.]*)?\s+import\s+(?P<names>.+)",
    re.S,
)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    parent: str | None = None

    @property
    def local_key(self) -> str:
        return f"{self.name}:{self.start_line}"

    @property
    def qualname(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass(frozen=True)
class ImportRef:
    module: str
    level: int = 0
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallRef:
    name: str
    line: int
    enclosing: str | None = None


@dataclass
class ParsedFile:
    path: str
    source: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    calls: list[CallRef] = field(default_factory=list)


def parse_python(path: str, source: str) -> ParsedFile:
    parsed = ParsedFile(path=path, source=source)
    tree = _PARSER.parse(source.encode("utf-8"))
    _visit(tree.root_node, path, parsed, class_name=None, function_depth=0, enclosing=None)
    return parsed


def _visit(
    node: Node,
    path: str,
    parsed: ParsedFile,
    *,
    class_name: str | None,
    function_depth: int,
    enclosing: Symbol | None,
) -> None:
    if node.type == "class_definition":
        symbol = _definition(node, path, kind="class", parent=class_name)
        if symbol is not None:
            parsed.symbols.append(symbol)
            child_class = symbol.name
            for child in node.named_children:
                _visit(
                    child,
                    path,
                    parsed,
                    class_name=child_class,
                    function_depth=0,
                    enclosing=symbol,
                )
        return

    if node.type == "function_definition":
        kind = "method" if class_name is not None and function_depth == 0 else "function"
        symbol = _definition(node, path, kind=kind, parent=class_name)
        if symbol is not None:
            parsed.symbols.append(symbol)
            for child in node.named_children:
                _visit(
                    child,
                    path,
                    parsed,
                    class_name=class_name,
                    function_depth=function_depth + 1,
                    enclosing=symbol,
                )
        return

    if node.type == "import_statement":
        parsed.imports.extend(_imports(node))
    elif node.type == "import_from_statement":
        parsed.imports.extend(_import_from(node))
    elif node.type == "call":
        name = _call_name(node)
        if name:
            parsed.calls.append(
                CallRef(
                    name=name,
                    line=node.start_point[0] + 1,
                    enclosing=enclosing.local_key if enclosing else None,
                )
            )

    for child in node.named_children:
        _visit(
            child,
            path,
            parsed,
            class_name=class_name,
            function_depth=function_depth,
            enclosing=enclosing,
        )


def _definition(node: Node, path: str, *, kind: str, parent: str | None) -> Symbol | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return Symbol(
        name=name_node.text.decode("utf-8"),
        kind=kind,
        path=path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        parent=parent,
    )


def _imports(node: Node) -> list[ImportRef]:
    modules: list[ImportRef] = []
    for child in node.named_children:
        if child.type == "dotted_name":
            modules.append(ImportRef(child.text.decode("utf-8")))
        elif child.type == "aliased_import":
            name = child.child_by_field_name("name")
            if name is not None:
                modules.append(ImportRef(name.text.decode("utf-8")))
    return modules


def _import_from(node: Node) -> list[ImportRef]:
    match = _IMPORT_FROM.match(node.text.decode("utf-8"))
    if match is None:
        return []

    names = []
    for raw_name in match.group("names").strip("() \n\t").split(","):
        clean = raw_name.split(" as ")[0].strip()
        if clean and clean != "*":
            names.append(clean.split(".")[-1])

    return [
        ImportRef(
            module=(match.group("module") or "").strip("."),
            level=len(match.group("dots")),
            names=tuple(names),
        )
    ]


def _call_name(node: Node) -> str | None:
    function = node.child_by_field_name("function")
    if function is None:
        return None
    if function.type == "identifier":
        return function.text.decode("utf-8")
    if function.type == "attribute":
        attribute = function.child_by_field_name("attribute")
        if attribute is not None:
            return attribute.text.decode("utf-8")
    return None
