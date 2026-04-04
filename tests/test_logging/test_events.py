"""Tests for structured log event models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentic_dev.logging.events import (
    AgentCompleteEvent,
    AgentEmptyRetryEvent,
    AgentFailedEvent,
    AgentStartEvent,
    CheckpointDecisionEvent,
    DocumentArchiveEvent,
    DocumentReadEvent,
    DocumentWriteEvent,
    LogEvent,
    PhaseTransitionEvent,
    PipelineCheckpointEvent,
    PipelineCompleteEvent,
    PipelineFailedEvent,
    PipelineStartEvent,
    PromptRenderedEvent,
    QACycleCompleteEvent,
    QACycleCorrectionEvent,
    QACycleStartEvent,
    QACycleVerdictEvent,
    SprintCompleteEvent,
    SprintFailedEvent,
    SprintPhaseEvent,
    SprintStartEvent,
    StateSaveEvent,
    StateLoadEvent,
)


# ---------------------------------------------------------------------------
# Base LogEvent
# ---------------------------------------------------------------------------


class TestLogEventBase:
    def test_instantiate_with_message_and_event_type(self) -> None:
        event = LogEvent(message="hello", event_type="test")
        assert event.message == "hello"
        assert event.event_type == "test"

    def test_timestamp_defaults_to_utc_now(self) -> None:
        before = datetime.now(timezone.utc)
        event = LogEvent(message="ts test", event_type="test")
        after = datetime.now(timezone.utc)
        assert before <= event.timestamp <= after
        assert event.timestamp.tzinfo is not None

    def test_run_id_defaults_to_empty_string(self) -> None:
        event = LogEvent(message="m", event_type="t")
        assert event.run_id == ""

    def test_project_name_defaults_to_empty_string(self) -> None:
        event = LogEvent(message="m", event_type="t")
        assert event.project_name == ""

    def test_level_defaults_to_info(self) -> None:
        event = LogEvent(message="m", event_type="t")
        assert event.level == "INFO"

    def test_flat_json_structure(self) -> None:
        """model_dump() should return only top-level keys (no nesting beyond simple types)."""
        event = LogEvent(message="flat", event_type="test")
        data = event.model_dump()
        for key, value in data.items():
            assert not isinstance(value, dict), (
                f"Key '{key}' is a nested dict in the base LogEvent dump"
            )


# ---------------------------------------------------------------------------
# Subclass default event_type values
# ---------------------------------------------------------------------------


# (event_class, expected_event_type, required_kwargs)
_SUBCLASS_CASES: list[tuple[type[LogEvent], str, dict]] = [
    (PipelineStartEvent, "pipeline_start", {
        "mode": "new", "phase": "ONBOARDING", "command_args": {},
    }),
    (PipelineCompleteEvent, "pipeline_complete", {
        "total_cost_usd": 1.0, "total_duration_s": 60.0, "sprint_count": 2,
    }),
    (PipelineFailedEvent, "pipeline_failed", {
        "error": "boom", "failed_at_phase": "DESIGN",
    }),
    (PipelineCheckpointEvent, "pipeline_checkpoint", {
        "phase": "DESIGN", "total_cost_usd": 0.5, "documents_produced": ["spec"],
    }),
    (PhaseTransitionEvent, "phase_transition", {
        "from_phase": "A", "to_phase": "B",
    }),
    (AgentStartEvent, "agent_start", {
        "agent_name": "be_dev", "model": "opus", "prompt_length": 100,
        "working_dir": "/tmp",
    }),
    (AgentCompleteEvent, "agent_complete", {
        "agent_name": "be_dev", "model": "opus", "duration_s": 10.0,
        "cost_usd": 0.25, "result_length": 500,
    }),
    (AgentFailedEvent, "agent_failed", {
        "agent_name": "be_dev", "model": "opus", "duration_s": 5.0,
        "exit_code": 1, "error": "timeout",
    }),
    (AgentEmptyRetryEvent, "agent_empty_retry", {
        "agent_name": "backend_developer", "attempt": 1, "max_retries": 1,
        "wait_seconds": 5.0,
    }),
    (QACycleStartEvent, "qa_cycle_start", {
        "action_agent": "be_dev", "qa_agent": "be_qa", "output_doc_name": "doc",
    }),
    (QACycleVerdictEvent, "qa_cycle_verdict", {
        "action_agent": "be_dev", "qa_agent": "be_qa", "issues_found": True,
    }),
    (QACycleCorrectionEvent, "qa_cycle_correction", {
        "action_agent": "be_dev", "correction_cost": 0.10,
    }),
    (QACycleCompleteEvent, "qa_cycle_complete", {
        "action_agent": "be_dev", "qa_agent": "be_qa",
        "corrected": False, "total_cost": 0.30,
    }),
    (SprintStartEvent, "sprint_start", {
        "sprint_number": 1, "sprint_name": "auth", "needs_integration": False,
    }),
    (SprintPhaseEvent, "sprint_phase", {
        "sprint_number": 1, "sub_phase": "backend",
    }),
    (SprintCompleteEvent, "sprint_complete", {
        "sprint_number": 1, "success": True, "total_cost": 0.50, "duration_s": 60.0,
    }),
    (SprintFailedEvent, "sprint_failed", {
        "sprint_number": 1, "error": "build failed", "partial_cost": 0.20,
    }),
    (DocumentWriteEvent, "document_write", {
        "doc_name": "spec", "content_length": 100, "path": "/tmp/spec.md",
    }),
    (DocumentReadEvent, "document_read", {
        "doc_name": "spec", "content_length": 100, "path": "/tmp/spec.md",
    }),
    (DocumentArchiveEvent, "document_archive", {
        "cycle_label": "sprint-1", "archive_path": "/tmp/archive",
    }),
    (StateSaveEvent, "state_save", {
        "phase": "DESIGN", "total_cost_usd": 0.5, "sprint_count": 1,
    }),
    (StateLoadEvent, "state_load", {
        "phase": "DESIGN", "total_cost_usd": 0.5,
    }),
    (PromptRenderedEvent, "prompt_rendered", {
        "template_name": "backend_developer.md.j2",
        "context_keys": ["spec", "code"], "output_length": 2000,
    }),
    (CheckpointDecisionEvent, "checkpoint_decision", {
        "phase": "DESIGN", "should_pause": False, "config_snapshot": {"k": "v"},
    }),
]


class TestSubclassDefaults:
    @pytest.mark.parametrize(
        "event_cls, expected_type, kwargs",
        _SUBCLASS_CASES,
        ids=[cls.__name__ for cls, _, _ in _SUBCLASS_CASES],
    )
    def test_default_event_type(
        self,
        event_cls: type[LogEvent],
        expected_type: str,
        kwargs: dict,
    ) -> None:
        event = event_cls(message="test", **kwargs)
        assert event.event_type == expected_type


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    @pytest.mark.parametrize(
        "event_cls, expected_type, kwargs",
        _SUBCLASS_CASES,
        ids=[cls.__name__ for cls, _, _ in _SUBCLASS_CASES],
    )
    def test_dump_json_then_validate_json_preserves_fields(
        self,
        event_cls: type[LogEvent],
        expected_type: str,
        kwargs: dict,
    ) -> None:
        original = event_cls(message="round-trip", **kwargs)
        json_str = original.model_dump_json()
        restored = event_cls.model_validate_json(json_str)
        assert restored == original

    def test_base_event_round_trip(self) -> None:
        original = LogEvent(message="base", event_type="custom")
        json_str = original.model_dump_json()
        restored = LogEvent.model_validate_json(json_str)
        assert restored == original


# ---------------------------------------------------------------------------
# Flat structure verification for subclasses
# ---------------------------------------------------------------------------


class TestFlatStructure:
    @pytest.mark.parametrize(
        "event_cls, expected_type, kwargs",
        _SUBCLASS_CASES,
        ids=[cls.__name__ for cls, _, _ in _SUBCLASS_CASES],
    )
    def test_model_dump_has_no_nested_model_objects(
        self,
        event_cls: type[LogEvent],
        expected_type: str,
        kwargs: dict,
    ) -> None:
        """All values in model_dump() should be primitives, lists, or simple dicts."""
        event = event_cls(message="flat check", **kwargs)
        data = event.model_dump()
        for key, value in data.items():
            assert not hasattr(value, "model_dump"), (
                f"Key '{key}' contains a nested Pydantic model"
            )
