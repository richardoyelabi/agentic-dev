"""Global settings, default paths, and constants for the agentic-dev agency."""

from pathlib import Path


DEFAULT_PROJECTS_DIR = Path.home() / "projects"

AGENCY_DIR = Path(__file__).parent.parent.parent

AGENT_DEFINITIONS_DIR = AGENCY_DIR / "src" / "agentic_dev" / "agents" / "definitions"
PROMPT_TEMPLATES_DIR = AGENCY_DIR / "src" / "agentic_dev" / "prompts" / "templates"
MCP_CONFIGS_DIR = AGENCY_DIR / "src" / "agentic_dev" / "agents" / "mcp_configs"

AGENTIC_DEV_METADATA_DIR = ".agentic-dev"
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
HISTORY_DIR = "history"
LOGS_DIR = "logs"
AGENT_DUMPS_DIR = "agent_dumps"
RUNS_DIR = "runs"
LATEST_SYMLINK = "latest"
SESSIONS_DIR = "sessions"

STATE_LOCK_FILE = ".state.lock"
DOCS_LOCK_FILE = ".docs.lock"
SESSIONS_LOCK_FILE = ".sessions.lock"

DOCS_DIR = "docs"
QA_REPORTS_DIR = "qa_reports"

DEFAULT_MAX_TURNS = 50

MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
}

DOCUMENT_SEPARATOR = "<!-- DOCUMENT: {name} -->"
DOCUMENT_SEPARATOR_PATTERN = r"<!-- DOCUMENT: (\w+) -->"
