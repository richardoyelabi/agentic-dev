"""Pydantic models for document metadata and taxonomy."""

from enum import StrEnum

from pydantic import BaseModel


class DocumentType(StrEnum):
    """All document types produced and consumed by the agency pipeline."""

    STRUCTURED_INPUT = "structured_input"
    FEATURES_REQUEST = "features_request"
    FRONTEND_SPEC = "frontend_spec"
    BACKEND_SPEC = "backend_spec"
    API_CONTRACT = "api_contract"
    SPRINT_PLAN = "sprint_plan"
    INTEGRATION_GUIDE = "integration_guide"
    QA_REPORT = "qa_report"
    UAT_REPORT = "uat_report"


DOCUMENT_FILENAMES: dict[DocumentType, str] = {
    DocumentType.STRUCTURED_INPUT: "structured_input.md",
    DocumentType.FEATURES_REQUEST: "features_request.md",
    DocumentType.FRONTEND_SPEC: "frontend_spec.md",
    DocumentType.BACKEND_SPEC: "backend_spec.md",
    DocumentType.API_CONTRACT: "api_contract.md",
    DocumentType.SPRINT_PLAN: "sprint_plan.md",
    DocumentType.INTEGRATION_GUIDE: "integration_guide.md",
    DocumentType.QA_REPORT: "qa_report.md",
    DocumentType.UAT_REPORT: "uat_report.md",
}


class DocumentMetadata(BaseModel):
    """Metadata associated with a document in the pipeline."""

    doc_type: DocumentType
    filename: str
    created_at: str
    updated_at: str
    produced_by: str
