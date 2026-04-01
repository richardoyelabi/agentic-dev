"""Checkpoint system for pausing the pipeline at configurable points."""

from pydantic import BaseModel

from agentic_dev.logging import get_event_logger, emit
from agentic_dev.logging.events import CheckpointDecisionEvent
from agentic_dev.state.models import PipelinePhase

_event_log = get_event_logger("checkpoint")


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
        result = config.after_design
    elif phase == PipelinePhase.SPRINTING and sprint_just_completed:
        result = config.after_each_sprint
    elif phase == PipelinePhase.UAT:
        result = config.before_uat
    else:
        result = False

    emit(_event_log, CheckpointDecisionEvent(
        phase=str(phase),
        should_pause=result,
        config_snapshot=config.model_dump(),
        message=f"Checkpoint at {phase}: {'pausing' if result else 'continuing'}",
    ))

    return result


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
