from pathlib import Path

import pytest

from ash_agent.intelligence.patching import (
    PatchEdit,
    PatchError,
    PatchProposal,
    apply_edits,
)


def proposal(*edits: PatchEdit) -> PatchProposal:
    return PatchProposal(summary="fix", edits=list(edits))


def edit(path: str, old: str, new: str) -> PatchEdit:
    return PatchEdit(path=path, old_text=old, new_text=new, reason="test")


class TestApplyEdits:
    def test_applies_single_edit_and_builds_diff(self, tmp_path: Path) -> None:
        (tmp_path / "core.py").write_text("def total():\n    return 100\n")

        applied = apply_edits(
            tmp_path,
            proposal(edit("core.py", "return 100", "return 99")),
        )

        assert applied.changed_files == ["core.py"]
        assert "return 99" in (tmp_path / "core.py").read_text()
        assert "--- a/core.py" in applied.diff
        assert "+    return 99" in applied.diff

    def test_multiple_edits_to_same_file(self, tmp_path: Path) -> None:
        (tmp_path / "core.py").write_text("a = 1\nb = 2\n")

        apply_edits(
            tmp_path,
            proposal(
                edit("core.py", "a = 1", "a = 10"),
                edit("core.py", "b = 2", "b = 20"),
            ),
        )

        assert (tmp_path / "core.py").read_text() == "a = 10\nb = 20\n"

    def test_rejects_missing_old_text(self, tmp_path: Path) -> None:
        (tmp_path / "core.py").write_text("a = 1\n")

        with pytest.raises(PatchError, match="found 0"):
            apply_edits(tmp_path, proposal(edit("core.py", "missing", "x")))

    def test_rejects_ambiguous_old_text(self, tmp_path: Path) -> None:
        (tmp_path / "core.py").write_text("x = 1\nx = 1\n")

        with pytest.raises(PatchError, match="found 2"):
            apply_edits(tmp_path, proposal(edit("core.py", "x = 1", "x = 2")))

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(PatchError, match="unsafe patch path"):
            apply_edits(tmp_path, proposal(edit("../secret.py", "a", "b")))

    def test_rejects_sensitive_file(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("TOKEN=x\n")

        with pytest.raises(PatchError, match="sensitive"):
            apply_edits(tmp_path, proposal(edit(".env", "TOKEN=x", "TOKEN=y")))

    def test_rejects_empty_edit_list(self, tmp_path: Path) -> None:
        with pytest.raises(PatchError, match="no edits"):
            apply_edits(tmp_path, PatchProposal(summary="empty", edits=[]))
