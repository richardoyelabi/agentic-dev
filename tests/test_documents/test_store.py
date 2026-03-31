"""Tests for the DocumentStore."""

import pytest

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
