from ash_agent.intelligence.chunking import build_chunks, tokenize
from ash_agent.intelligence.parser import parse_python

SOURCE = """\
import os
from .utils import helper
from pkg.sub import thing


class Calculator:
    def calculate_total(self, subtotal, discount, tax_rate):
        value = helper(subtotal)
        return value


def main():
    calc = Calculator()
    calc.calculate_total(100, 10, 0.1)
"""


class TestParser:
    def test_extracts_symbols(self) -> None:
        parsed = parse_python("src/calculator.py", SOURCE)

        names = {(symbol.name, symbol.kind, symbol.parent) for symbol in parsed.symbols}
        assert ("Calculator", "class", None) in names
        assert ("calculate_total", "method", "Calculator") in names
        assert ("main", "function", None) in names

    def test_extracts_imports(self) -> None:
        parsed = parse_python("src/calculator.py", SOURCE)

        imports = {(ref.module, ref.level) for ref in parsed.imports}
        assert ("os", 0) in imports
        assert ("utils", 1) in imports
        assert ("pkg.sub", 0) in imports
        assert next(ref for ref in parsed.imports if ref.module == "utils").names == ("helper",)

    def test_extracts_calls_with_enclosing_symbol(self) -> None:
        parsed = parse_python("src/calculator.py", SOURCE)

        calls = {(call.name, call.enclosing) for call in parsed.calls}
        assert ("helper", "calculate_total:7") in calls
        assert ("Calculator", "main:12") in calls
        assert ("calculate_total", "main:12") in calls

    def test_builds_symbol_chunks_and_skips_class_duplicate(self) -> None:
        parsed = parse_python("src/calculator.py", SOURCE)
        chunks = build_chunks(
            parsed,
            repository="The-Tourist-170/dummy_project",
            commit_sha="a" * 40,
            max_lines=60,
            overlap=10,
        )

        kinds = {(chunk.path, chunk.kind, chunk.symbols) for chunk in chunks}
        assert ("src/calculator.py", "method", ("Calculator.calculate_total",)) in kinds
        assert ("src/calculator.py", "function", ("main",)) in kinds
        assert not any(chunk.kind == "class" for chunk in chunks)
        assert any(chunk.kind == "module" for chunk in chunks)

    def test_chunk_ids_are_stable(self) -> None:
        parsed = parse_python("src/calculator.py", SOURCE)
        first = build_chunks(parsed, repository="owner/repo", commit_sha="b" * 40)
        second = build_chunks(parsed, repository="owner/repo", commit_sha="b" * 40)

        assert [chunk.id for chunk in first] == [chunk.id for chunk in second]

    def test_tokenizer_splits_identifiers(self) -> None:
        assert tokenize("calculate_total") == ["calculate", "total"]
        assert tokenize("HTTPServer") == ["httpserver"]
        assert tokenize("camelCaseName") == ["camel", "case", "name"]
