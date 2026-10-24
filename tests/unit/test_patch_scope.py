from pathlib import Path

from ash_agent.intelligence.patch_scope import audit_patch_scope
from ash_agent.intelligence.patching import PatchEdit


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "src" / "demo").mkdir(parents=True)
    (root / "scripts" / "compose_smoke.py").write_text("def main():\n    print(message)\n")
    (root / "src" / "demo" / "core.py").write_text(
        "def add(a, b):\n    return a - b\n\ndef multiply(a, b):\n    return a + b\n"
    )
    return root


def edit(path: str, old: str, new: str) -> PatchEdit:
    return PatchEdit(path=path, old_text=old, new_text=new, reason="test")


class TestPatchScope:
    def test_rejects_file_outside_scope(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        violations = audit_patch_scope(
            [edit("src/demo/core.py", "return a + b", "return a * b")],
            root,
            ["scripts/compose_smoke.py"],
            [],
        )

        assert violations
        assert "outside issue scope" in violations[0]

    def test_rejects_unrelated_symbol_in_allowed_file(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        violations = audit_patch_scope(
            [edit("src/demo/core.py", "return a + b", "return a * b")],
            root,
            ["src/demo/core.py"],
            ["add"],
        )

        assert violations
        assert "allowed symbols" in violations[0]

    def test_allows_scoped_symbol(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        violations = audit_patch_scope(
            [edit("src/demo/core.py", "return a - b", "return a + b")],
            root,
            ["src/demo/core.py"],
            ["add"],
        )

        assert violations == []

    def test_never_allows_test_edits(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        (root / "tests").mkdir()
        (root / "tests" / "test_core.py").write_text("def test_add():\n    assert True\n")
        violations = audit_patch_scope(
            [
                edit(
                    "tests/test_core.py",
                    "assert True",
                    "assert 1 == 1",
                )
            ],
            root,
            ["tests/test_core.py"],
            [],
        )

        assert violations
        assert "test files are not editable" in violations[0]

    def test_empty_scope_fails_closed(self, tmp_path: Path) -> None:
        root = repo(tmp_path)
        violations = audit_patch_scope(
            [edit("src/demo/core.py", "return a + b", "return a * b")],
            root,
            [],
            [],
        )

        assert violations == ["scope has no allowed files"]
