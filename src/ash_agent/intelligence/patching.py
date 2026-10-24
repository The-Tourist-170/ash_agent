from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field


class PatchEdit(BaseModel):
    path: str = Field(description="Repository-relative file path.")
    old_text: str = Field(description="Exact text that must occur once in the file.")
    new_text: str = Field(description="Replacement text.")
    reason: str = Field(description="Why this edit fixes the failure.")


class PatchProposal(BaseModel):
    summary: str = Field(description="Short explanation of the repair.")
    edits: list[PatchEdit] = Field(
        default_factory=list,
        description="Minimal exact search/replace edits.",
    )


class PatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppliedPatch:
    summary: str
    diff: str
    changed_files: list[str]
    files: dict[str, str]


def apply_edits(root: Path, proposal: PatchProposal, *, max_edits: int = 20) -> AppliedPatch:
    if not proposal.edits:
        raise PatchError("patch proposal contained no edits")
    if len(proposal.edits) > max_edits:
        raise PatchError(f"patch proposal has too many edits ({len(proposal.edits)})")

    originals: dict[str, str] = {}
    current: dict[str, str] = {}

    for edit in proposal.edits:
        path = _safe_path(edit.path)
        if path not in current:
            file_path = root / path
            if not file_path.is_file():
                raise PatchError(f"edit targets missing file: {path}")
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise PatchError(f"edit targets non-text file: {path}") from exc
            originals[path] = content
            current[path] = content

        occurrences = current[path].count(edit.old_text)
        if occurrences != 1:
            raise PatchError(f"old_text must occur exactly once in {path}; found {occurrences}")
        if not edit.new_text.strip():
            raise PatchError(f"edit for {path} has empty new_text")
        current[path] = current[path].replace(edit.old_text, edit.new_text, 1)

    for path, content in current.items():
        (root / path).write_text(content, encoding="utf-8")

    diff_parts: list[str] = []
    for path in sorted(current):
        diff_parts.extend(
            difflib.unified_diff(
                originals[path].splitlines(keepends=True),
                current[path].splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )

    return AppliedPatch(
        summary=proposal.summary,
        diff="".join(diff_parts),
        changed_files=sorted(current),
        files=current,
    )


def _safe_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PatchError(f"unsafe patch path: {path!r}")
    if not candidate.parts:
        raise PatchError("empty patch path")
    lowered = path.lower()
    if lowered.startswith(".env") or lowered in {
        ".netrc",
        "id_rsa",
        "id_ed25519",
    }:
        raise PatchError(f"refusing to edit sensitive file: {path}")
    return candidate.as_posix()
