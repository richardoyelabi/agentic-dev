"""Checkpoint system for pausing the pipeline at configurable points."""

from pydantic import BaseModel

from agentic_dev.state.models import PipelinePhase


class CheckpointConfig(BaseModel):
    """Controls which pipeline phases trigger a pause for human review."""

    after_design: bool = True
    after_each_sprint: bool = False
    before_uat: bool = False


def should_pause(
    phase: PipelinePhase,
    config: CheckpointConfig,
    sprint_just_completed: bool = False,
) -> bool:
    """Return True if the pipeline should pause at the given phase."""
    if phase == PipelinePhase.DESIGN_CHECKPOINT:
        return config.after_design
    if phase == PipelinePhase.SPRINTING and sprint_just_completed:
        return config.after_each_sprint
    if phase == PipelinePhase.UAT:
        return config.before_uat
    return False


def from_autonomy_level(level: str) -> CheckpointConfig:
    """Create a CheckpointConfig from a named autonomy level.

    Levels:
        "full"    - no checkpoints, fully autonomous
        "default" - pause after design phase only
        "maximum" - pause at every available checkpoint
    """
    if level == "full":
        return CheckpointConfig(
            after_design=False,
            after_each_sprint=False,
            before_uat=False,
        )
    if level == "maximum":
        return CheckpointConfig(
            after_design=True,
            after_each_sprint=True,
            before_uat=True,
        )
    # "default" or any unrecognized level
    return CheckpointConfig(
        after_design=True,
        after_each_sprint=False,
        before_uat=False,
    )
