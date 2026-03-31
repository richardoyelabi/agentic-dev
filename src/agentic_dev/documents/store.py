"""Document storage for reading and writing pipeline documents."""

import shutil
from pathlib import Path

from agentic_dev.exceptions import DocumentError


class DocumentStore:
    """Manages reading and writing documents in a project's docs directory."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.docs_dir = project_dir / "docs"

    def _resolve(self, doc_name: str) -> Path:
        """Resolve a document name to a path, auto-appending .md if needed."""
        if not doc_name.endswith(".md"):
            doc_name = f"{doc_name}.md"
        return self.docs_dir / doc_name

    def write(self, doc_name: str, content: str) -> Path:
        """Write a document to the docs directory.

        If doc_name starts with "qa_reports/", writes to the qa_reports
        subdirectory within docs. Creates directories as needed.
        Automatically appends .md extension if not present.
        """
        target = self._resolve(doc_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read(self, doc_name: str) -> str:
        """Read a document's content.

        Raises DocumentError if the document does not exist.
        """
        target = self._resolve(doc_name)
        if not target.exists():
            raise DocumentError(f"Document not found: {target}")
        return target.read_text(encoding="utf-8")

    def exists(self, doc_name: str) -> bool:
        """Check whether a document exists."""
        return self._resolve(doc_name).exists()

    def list_documents(self) -> list[str]:
        """List all .md files in the docs directory (non-recursive top level)."""
        if not self.docs_dir.exists():
            return []
        return sorted(f.name for f in self.docs_dir.glob("*.md"))

    def list_qa_reports(self) -> list[str]:
        """List all .md files in the qa_reports subdirectory."""
        qa_dir = self.docs_dir / "qa_reports"
        if not qa_dir.exists():
            return []
        return sorted(f.name for f in qa_dir.glob("*.md"))

    def archive_cycle(self, cycle_label: str) -> Path:
        """Copy all current docs to docs/archive/{cycle_label}/.

        Copies everything in docs/ except the archive/ directory itself.
        Returns the archive directory path.
        """
        archive_dir = self.docs_dir / "archive" / cycle_label
        archive_dir.mkdir(parents=True, exist_ok=True)

        if not self.docs_dir.exists():
            return archive_dir

        for item in self.docs_dir.iterdir():
            if item.name == "archive":
                continue
            dest = archive_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        return archive_dir
