"""Tests for document metadata models."""

from agentic_dev.documents.models import (
    DOCUMENT_FILENAMES,
    DocumentMetadata,
    DocumentType,
)


class TestDocumentType:
    def test_enum_values(self):
        assert DocumentType.STRUCTURED_INPUT == "structured_input"
        assert DocumentType.FEATURES_REQUEST == "features_request"
        assert DocumentType.FRONTEND_SPEC == "frontend_spec"
        assert DocumentType.BACKEND_SPEC == "backend_spec"
        assert DocumentType.API_CONTRACT == "api_contract"
        assert DocumentType.SPRINT_PLAN == "sprint_plan"
        assert DocumentType.INTEGRATION_GUIDE == "integration_guide"
        assert DocumentType.QA_REPORT == "qa_report"
        assert DocumentType.UAT_REPORT == "uat_report"

    def test_enum_count(self):
        assert len(DocumentType) == 9


class TestDocumentFilenames:
    def test_all_types_have_filenames(self):
        for doc_type in DocumentType:
            assert doc_type in DOCUMENT_FILENAMES

    def test_filenames_are_markdown(self):
        for filename in DOCUMENT_FILENAMES.values():
            assert filename.endswith(".md")

    def test_specific_mappings(self):
        assert DOCUMENT_FILENAMES[DocumentType.STRUCTURED_INPUT] == "structured_input.md"
        assert DOCUMENT_FILENAMES[DocumentType.API_CONTRACT] == "api_contract.md"
        assert DOCUMENT_FILENAMES[DocumentType.SPRINT_PLAN] == "sprint_plan.md"


class TestDocumentMetadata:
    def test_creation(self):
        metadata = DocumentMetadata(
            doc_type=DocumentType.FEATURES_REQUEST,
            filename="features_request.md",
            created_at="2026-03-28T10:00:00Z",
            updated_at="2026-03-28T10:00:00Z",
            produced_by="feature_analyst",
        )
        assert metadata.doc_type == DocumentType.FEATURES_REQUEST
        assert metadata.filename == "features_request.md"
        assert metadata.produced_by == "feature_analyst"

    def test_metadata_with_updated_timestamp(self):
        metadata = DocumentMetadata(
            doc_type=DocumentType.API_CONTRACT,
            filename="api_contract.md",
            created_at="2026-03-28T10:00:00Z",
            updated_at="2026-03-28T12:00:00Z",
            produced_by="architect",
        )
        assert metadata.created_at != metadata.updated_at
        assert metadata.updated_at == "2026-03-28T12:00:00Z"
