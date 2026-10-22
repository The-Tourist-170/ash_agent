from ash_agent.intelligence.parser import ImportRef
from ash_agent.persistence.neo4j import _resolve_import


class TestImportResolution:
    def test_absolute_import_resolves_from_repository_root(self) -> None:
        paths = {"pkg/utils.py", "pkg/sub/mod.py"}

        target = _resolve_import("pkg/sub/mod.py", ImportRef("pkg.utils"), paths)

        assert target == "pkg/utils.py"

    def test_relative_import_resolves_from_current_package(self) -> None:
        paths = {"pkg/sub/utils.py", "pkg/sub/mod.py"}

        target = _resolve_import("pkg/sub/mod.py", ImportRef("utils", level=1), paths)

        assert target == "pkg/sub/utils.py"

    def test_relative_parent_import_walks_up(self) -> None:
        paths = {"pkg/utils.py", "pkg/sub/mod.py"}

        target = _resolve_import("pkg/sub/mod.py", ImportRef("utils", level=2), paths)

        assert target == "pkg/utils.py"

    def test_relative_package_import_resolves_to_init(self) -> None:
        paths = {"pkg/__init__.py", "pkg/mod.py"}

        target = _resolve_import("pkg/mod.py", ImportRef("", level=1), paths)

        assert target == "pkg/__init__.py"

    def test_missing_import_returns_none(self) -> None:
        assert _resolve_import("pkg/mod.py", ImportRef("missing"), {"pkg/mod.py"}) is None
