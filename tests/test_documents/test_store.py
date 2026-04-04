"""Tests for the DocumentStore."""

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from agentic_dev.config import AGENTIC_DEV_METADATA_DIR, DOCS_LOCK_FILE
from agentic_dev.documents.store import DocumentStore
from agentic_dev.exceptions import DocumentError


@pytest.fixture
def store(tmp_path):
    return DocumentStore(project_dir=tmp_path)


class TestWriteAndRead:
    def test_roundtrip(self, store):
        content = "# Features Request\n\nSome content here."
        path = store.write("features_request.md", content)

        assert path.exists()
        assert store.read("features_request.md") == content

    def test_write_creates_docs_directory(self, store):
        assert not store.docs_dir.exists()
        store.write("test.md", "content")
        assert store.docs_dir.exists()

    def test_write_to_qa_reports_subdirectory(self, store):
        content = "# QA Report\n\nAll good."
        path = store.write("qa_reports/architect_qa_sprint1.md", content)

        assert path.exists()
        assert "qa_reports" in str(path)
        assert store.read("qa_reports/architect_qa_sprint1.md") == content

    def test_write_creates_qa_reports_directory(self, store):
        qa_dir = store.docs_dir / "qa_reports"
        assert not qa_dir.exists()
        store.write("qa_reports/report.md", "content")
        assert qa_dir.exists()


class TestReadMissing:
    def test_read_nonexistent_raises_document_error(self, store):
        with pytest.raises(DocumentError, match="Document not found"):
            store.read("nonexistent.md")


class TestExists:
    def test_exists_returns_true_for_existing_document(self, store):
        store.write("test.md", "content")
        assert store.exists("test.md") is True

    def test_exists_returns_false_for_missing_document(self, store):
        assert store.exists("missing.md") is False


class TestListDocuments:
    def test_list_documents_returns_md_files(self, store):
        store.write("alpha.md", "a")
        store.write("beta.md", "b")

        result = store.list_documents()
        assert result == ["alpha.md", "beta.md"]

    def test_list_documents_empty_when_no_docs_dir(self, store):
        assert store.list_documents() == []

    def test_list_documents_excludes_subdirectory_files(self, store):
        store.write("top_level.md", "content")
        store.write("qa_reports/nested.md", "content")

        result = store.list_documents()
        assert result == ["top_level.md"]


class TestListQaReports:
    def test_list_qa_reports(self, store):
        store.write("qa_reports/report_a.md", "a")
        store.write("qa_reports/report_b.md", "b")

        result = store.list_qa_reports()
        assert result == ["report_a.md", "report_b.md"]

    def test_list_qa_reports_empty_when_no_dir(self, store):
        assert store.list_qa_reports() == []


class TestArchiveCycle:
    def test_archive_cycle_copies_docs(self, store):
        store.write("features.md", "features content")
        store.write("architecture.md", "arch content")
        store.write("qa_reports/features_qa.md", "qa content")

        archive_dir = store.archive_cycle("cycle_0")

        assert (archive_dir / "features.md").read_text(encoding="utf-8") == "features content"
        assert (archive_dir / "architecture.md").read_text(encoding="utf-8") == "arch content"
        assert (archive_dir / "qa_reports" / "features_qa.md").read_text(encoding="utf-8") == "qa content"

    def test_archive_cycle_skips_archive_dir(self, store):
        store.write("features.md", "content")
        store.archive_cycle("cycle_0")

        # Archive again — the archive/ directory itself should not be copied
        archive_dir = store.archive_cycle("cycle_1")

        assert not (archive_dir / "archive").exists()

    def test_archive_cycle_works_with_empty_docs(self, store):
        archive_dir = store.archive_cycle("cycle_0")

        assert archive_dir.exists()

    def test_archive_cycle_preserves_originals(self, store):
        store.write("features.md", "original")
        store.archive_cycle("cycle_0")

        assert store.read("features.md") == "original"


class TestAutoMdExtension:
    """DocumentStore should auto-append .md extension for easier viewing."""

    def test_write_without_extension_creates_md_file(self, store):
        path = store.write("features", "content")
        assert path.suffix == ".md"
        assert path.name == "features.md"

    def test_read_without_extension_finds_md_file(self, store):
        store.write("features", "content")
        assert store.read("features") == "content"

    def test_exists_without_extension_finds_md_file(self, store):
        store.write("features", "content")
        assert store.exists("features") is True

    def test_write_with_md_extension_does_not_double(self, store):
        path = store.write("features.md", "content")
        assert path.name == "features.md"
        assert not (store.docs_dir / "features.md.md").exists()

    def test_roundtrip_mixed_extensions(self, store):
        """Write without extension, read with extension, and vice versa."""
        store.write("features", "content A")
        assert store.read("features.md") == "content A"

        store.write("architecture.md", "content B")
        assert store.read("architecture") == "content B"

    def test_list_documents_finds_auto_extended_files(self, store):
        store.write("features", "a")
        store.write("architecture", "b")
        result = store.list_documents()
        assert "architecture.md" in result
        assert "features.md" in result

    def test_qa_reports_auto_extended(self, store):
        store.write("qa_reports/sprint_1_backend", "report")
        assert store.read("qa_reports/sprint_1_backend") == "report"
        result = store.list_qa_reports()
        assert "sprint_1_backend.md" in result


def _write_doc(project_dir_str: str, doc_name: str, content: str, ready_fd: int) -> None:
    """Helper: write a document and signal readiness."""
    store = DocumentStore(project_dir=Path(project_dir_str))
    os.write(ready_fd, b"r")
    time.sleep(0.05)
    store.write(doc_name, content)


class TestDocumentLocking:
    def test_write_creates_lock_file(self, store: DocumentStore, tmp_path: Path) -> None:
        store.write("test.md", "content")
        lock_path = tmp_path / AGENTIC_DEV_METADATA_DIR / DOCS_LOCK_FILE
        assert lock_path.exists()

    def test_read_creates_lock_file(self, store: DocumentStore, tmp_path: Path) -> None:
        store.write("test.md", "content")
        lock_path = tmp_path / AGENTIC_DEV_METADATA_DIR / DOCS_LOCK_FILE
        # Remove lock to verify read recreates it
        lock_path.unlink()
        store.read("test.md")
        assert lock_path.exists()

    def test_concurrent_writes_serialized(
        self, store: DocumentStore, tmp_path: Path
    ) -> None:
        """Two concurrent writes should not corrupt documents."""
        ready_r1, ready_w1 = os.pipe()
        ready_r2, ready_w2 = os.pipe()

        p1 = multiprocessing.Process(
            target=_write_doc,
            args=(str(tmp_path), "shared_doc", "content from p1", ready_w1),
        )
        p2 = multiprocessing.Process(
            target=_write_doc,
            args=(str(tmp_path), "shared_doc", "content from p2", ready_w2),
        )

        p1.start()
        p2.start()
        os.close(ready_w1)
        os.close(ready_w2)

        os.read(ready_r1, 1)
        os.read(ready_r2, 1)
        os.close(ready_r1)
        os.close(ready_r2)

        p1.join(timeout=5)
        p2.join(timeout=5)

        assert p1.exitcode == 0
        assert p2.exitcode == 0

        # Document should contain one complete write, not corrupted data
        content = store.read("shared_doc")
        assert content in ("content from p1", "content from p2")
