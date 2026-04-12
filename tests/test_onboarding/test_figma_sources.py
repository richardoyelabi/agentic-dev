"""Tests for Figma source persistence."""

from pathlib import Path

import pytest

from agentic_dev.config import AGENTIC_DEV_METADATA_DIR
from agentic_dev.documents.store import DocumentStore
from agentic_dev.onboarding.figma import write_figma_sources
from agentic_dev.onboarding.models import AnnotatedSource


@pytest.fixture
def doc_store(tmp_path: Path) -> DocumentStore:
    """Create a DocumentStore backed by a temporary directory."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "docs").mkdir()
    (project_dir / AGENTIC_DEV_METADATA_DIR).mkdir()
    return DocumentStore(project_dir)


class TestWriteFigmaSources:
    def test_writes_single_source_with_annotation(self, doc_store: DocumentStore) -> None:
        sources = [AnnotatedSource(value="https://figma.com/file/abc123", annotation="Main UI")]

        write_figma_sources(doc_store, sources)

        content = doc_store.read("figma_sources")
        assert "https://figma.com/file/abc123" in content
        assert "Main UI" in content

    def test_writes_multiple_sources(self, doc_store: DocumentStore) -> None:
        sources = [
            AnnotatedSource(value="https://figma.com/file/abc", annotation="App UI"),
            AnnotatedSource(value="https://figma.com/file/def", annotation="Admin"),
        ]

        write_figma_sources(doc_store, sources)

        content = doc_store.read("figma_sources")
        assert "https://figma.com/file/abc" in content
        assert "App UI" in content
        assert "https://figma.com/file/def" in content
        assert "Admin" in content

    def test_writes_source_without_annotation(self, doc_store: DocumentStore) -> None:
        sources = [AnnotatedSource(value="https://figma.com/file/xyz")]

        write_figma_sources(doc_store, sources)

        content = doc_store.read("figma_sources")
        assert "https://figma.com/file/xyz" in content
        assert "Annotation:" not in content

    def test_document_has_header(self, doc_store: DocumentStore) -> None:
        sources = [AnnotatedSource(value="https://figma.com/file/abc")]

        write_figma_sources(doc_store, sources)

        content = doc_store.read("figma_sources")
        assert content.startswith("# Figma Sources")

    def test_overwrites_existing_document(self, doc_store: DocumentStore) -> None:
        old_sources = [AnnotatedSource(value="https://figma.com/file/old")]
        new_sources = [AnnotatedSource(value="https://figma.com/file/new")]

        write_figma_sources(doc_store, old_sources)
        write_figma_sources(doc_store, new_sources)

        content = doc_store.read("figma_sources")
        assert "https://figma.com/file/new" in content
        assert "https://figma.com/file/old" not in content

    def test_empty_sources_list_does_not_write(self, doc_store: DocumentStore) -> None:
        write_figma_sources(doc_store, [])

        assert not doc_store.exists("figma_sources")
