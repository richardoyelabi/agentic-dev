"""Codebase analyzer for onboarding existing projects into the agency workflow.

Uses a dedicated Claude agent to analyze an existing codebase and produce
a Codebase Analysis document that feeds into the Input Processor.
"""

from pathlib import Path

from agentic_dev.claude.runner import ClaudeResult, ClaudeRunner
from agentic_dev.orchestrator.agent_bridge import AgentRunConfig


ANALYZER_PROMPT = """\
You are an expert codebase analyst. Analyze the codebase in your current working directory \
and produce a structured Codebase Analysis document.

Examine the project structure, dependencies, code patterns, and architecture. \
Produce your analysis in the following format:

# Codebase Analysis

## Tech Stack
- Frontend: <detected framework and version, or "N/A">
- Backend: <detected framework and version, or "N/A">
- Database: <detected database, or "N/A">
- Language(s): <detected programming languages>

## Architecture
### Routes/Endpoints
- <list discovered routes or API endpoints>

### Data Models
- <list discovered data models/schemas>

### UI Components
- <list discovered UI components, if applicable>

## Patterns & Conventions
- <coding patterns observed: naming conventions, project structure, testing approach, etc.>

## Dependencies
- <key dependencies and their purposes>

## Notes
- <anything notable about the codebase that would help in planning changes>
"""


async def analyze_codebase(
    claude: ClaudeRunner,
    codebase_path: Path,
) -> ClaudeResult:
    """Analyze an existing codebase using a Claude agent.

    Args:
        claude: The ClaudeRunner instance.
        codebase_path: Path to the existing codebase to analyze.

    Returns:
        ClaudeResult containing the Codebase Analysis document.
    """
    config = AgentRunConfig(
        name="onboarding_analyzer",
        model="sonnet",
        permission_mode="plan",
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=30,
        use_bare_mode=True,
        mcp_config=None,
        system_prompt=None,
    )

    return await claude.run(
        agent=config,
        prompt=ANALYZER_PROMPT,
        working_dir=codebase_path,
    )
