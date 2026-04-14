"""Document storage for reading and writing pipeline documents."""

from pathlib import Path

from agentic_dev.concurrency import file_lock
from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, DOCS_LOCK_FILE
from agentic_dev.exceptions import DocumentError
from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import DocumentWriteEvent, DocumentReadEvent

_event_log = get_event_logger("documents")


class DocumentStore:
    """Manages reading and writing documents in a project's docs directory."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.docs_dir = project_dir / "docs"
        self.lock_file = project_dir / AGENTIC_DEV_METADATA_DIR / DOCS_LOCK_FILE

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
        with file_lock(self.lock_file):
            target.write_text(content, encoding="utf-8")
        emit(_event_log, DocumentWriteEvent(
            doc_name=doc_name,
            content_length=len(content),
            path=str(target),
            message=f"Wrote {doc_name} ({len(content)} chars)",
        ))
        return target

    def read(self, doc_name: str) -> str:
        """Read a document's content.

        Raises DocumentError if the document does not exist.
        """
        target = self._resolve(doc_name)
        if not target.exists():
            raise DocumentError(f"Document not found: {target}")
        with file_lock(self.lock_file, shared=True):
            content = target.read_text(encoding="utf-8")
        emit(_event_log, DocumentReadEvent(
            doc_name=doc_name,
            content_length=len(content),
            path=str(target),
            message=f"Read {doc_name} ({len(content)} chars)",
        ))
        return content

    def exists(self, doc_name: str) -> bool:
        """Check whether a document exists."""
        return self._resolve(doc_name).exists()

    def delete(self, doc_name: str) -> None:
        """Delete a document from the docs directory.

        Silently succeeds if the document does not exist.
        """
        target = self._resolve(doc_name)
        if target.exists():
            with file_lock(self.lock_file):
                target.unlink()

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

