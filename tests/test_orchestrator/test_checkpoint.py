"""Tests for the checkpoint system."""


from agentic_dev.orchestrator.checkpoint import (
    CheckpointConfig,
    from_autonomy_level,
    should_pause,
)
from agentic_dev.state.models import PipelinePhase


class TestShouldPause:
    """Tests for the should_pause function."""

    def test_design_checkpoint_pauses_with_default_config(self):
        config = CheckpointConfig()
        assert should_pause(PipelinePhase.DESIGN_CHECKPOINT, config) is True

    def test_design_checkpoint_skipped_when_disabled(self):
        config = CheckpointConfig(after_design=False)
        assert should_pause(PipelinePhase.DESIGN_CHECKPOINT, config) is False

    def test_sprint_completion_pauses_when_enabled(self):
        config = CheckpointConfig(after_each_sprint=True)
        assert (
            should_pause(
                PipelinePhase.SPRINTING, config, sprint_just_completed=True
            )
            is True
        )

    def test_sprint_no_pause_without_completion_flag(self):
        config = CheckpointConfig(after_each_sprint=True)
        assert (
            should_pause(
                PipelinePhase.SPRINTING, config, sprint_just_completed=False
            )
            is False
        )

    def test_sprint_no_pause_when_disabled(self):
        config = CheckpointConfig(after_each_sprint=False)
        assert (
            should_pause(
                PipelinePhase.SPRINTING, config, sprint_just_completed=True
            )
            is False
        )

    def test_uat_pauses_when_enabled(self):
        config = CheckpointConfig(before_uat=True)
        assert should_pause(PipelinePhase.UAT, config) is True

    def test_uat_no_pause_when_disabled(self):
        config = CheckpointConfig(before_uat=False)
        assert should_pause(PipelinePhase.UAT, config) is False

    def test_unrelated_phase_never_pauses(self):
        config = CheckpointConfig(
            after_design=True, after_each_sprint=True, before_uat=True
        )
        assert should_pause(PipelinePhase.ARCHITECTURE, config) is False


class TestFromAutonomyLevel:
    """Tests for the from_autonomy_level factory."""

    def test_full_disables_all_checkpoints(self):
        config = from_autonomy_level("full")
        assert config.after_design is False
        assert config.after_each_sprint is False
        assert config.before_uat is False

    def test_default_enables_only_after_design(self):
        config = from_autonomy_level("default")
        assert config.after_design is True
        assert config.after_each_sprint is False
        assert config.before_uat is False

    def test_maximum_enables_all_checkpoints(self):
        config = from_autonomy_level("maximum")
        assert config.after_design is True
        assert config.after_each_sprint is True
        assert config.before_uat is True

    def test_unknown_level_falls_back_to_default(self):
        config = from_autonomy_level("unknown")
        assert config.after_design is True
        assert config.after_each_sprint is False
        assert config.before_uat is False
