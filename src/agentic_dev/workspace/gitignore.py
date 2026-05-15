"""Managed-block ``.gitignore`` maintenance.

``ensure_managed_gitignore`` writes a tagged block into a project's
``.gitignore`` so agentic-dev's metadata directories never leak into user
commits. The block is idempotent: re-running with the same entries is a
no-op. Editing the entry list updates the block in place. If the user
deletes the closing marker, that is treated as an explicit opt-out and the
block is never re-added.
"""

from __future__ import annotations

from pathlib import Path

BLOCK_START = "# >>> agentic-dev managed >>>"
BLOCK_END = "# <<< agentic-dev managed <<<"
_BLOCK_HEADER = (
    "# Managed by agentic-dev. Delete the closing marker to opt out;"
    " the block will not be re-added."
)


def ensure_managed_gitignore(project_root: Path, entries: list[str]) -> bool:
    """Maintain a managed ``.gitignore`` block in ``project_root``.

    Returns True if ``.gitignore`` was created or modified, False otherwise.

    No-op (returns False) when ``project_root/.git`` is absent. If the user
    has removed the closing marker, the function leaves the file untouched.
    Entries that already appear elsewhere in the file are not duplicated
    inside the managed block.
    """
    if not (project_root / ".git").exists():
        return False

    gitignore = project_root / ".gitignore"
    original = gitignore.read_text() if gitignore.exists() else ""

    has_open = BLOCK_START in original
    has_close = BLOCK_END in original
    if has_open and not has_close:
        return False

    user_lines, existing_block = _split_block(original)
    user_entries = {line.strip() for line in user_lines if line.strip()}
    block_entries = [e for e in entries if e not in user_entries]
    new_block = _format_block(block_entries) if block_entries else ""

    if new_block == existing_block:
        return False

    user_text = "".join(user_lines)
    if user_text and not user_text.endswith("\n"):
        user_text += "\n"

    pieces: list[str] = []
    if user_text:
        pieces.append(user_text)
    if new_block:
        if pieces:
            pieces.append("\n")
        pieces.append(new_block)

    gitignore.write_text("".join(pieces))
    return True


def _split_block(contents: str) -> tuple[list[str], str]:
    """Return (user_lines, block_text) where block_text includes its markers."""
    if BLOCK_START not in contents or BLOCK_END not in contents:
        return contents.splitlines(keepends=True), ""

    start_idx = contents.index(BLOCK_START)
    end_idx = contents.index(BLOCK_END) + len(BLOCK_END)
    if end_idx < len(contents) and contents[end_idx] == "\n":
        end_idx += 1

    before = contents[:start_idx]
    block = contents[start_idx:end_idx]
    after = contents[end_idx:]
    user = (before + after).rstrip("\n")
    user_lines = user.splitlines(keepends=True)
    if user and not user.endswith("\n"):
        user_lines = user_lines[:-1] + [user_lines[-1] + "\n"]
    return user_lines, block


def _format_block(entries: list[str]) -> str:
    lines = [BLOCK_START, _BLOCK_HEADER]
    lines.extend(entries)
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"
