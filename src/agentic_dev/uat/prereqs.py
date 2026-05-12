"""Runtime prereq probes for per-kind UAT agents.

Validates that the driver tools each UAT agent relies on are present and
functional — not just on PATH. Binary-present-but-runtime-missing is a
common failure mode (e.g. Maestro installed without a running emulator),
and the probes here distinguish those cases so the engine can force
``Overall: FAIL`` under ``uat_mode: full`` via the validator.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from agentic_dev.logging import emit, get_event_logger
from agentic_dev.logging.events import UATPrereqValidationEvent
from agentic_dev.mcp.claude_settings import (
    ClaudeMCPEnvironment,
    discover_mcp_servers,
    find_server_for_service,
)
from agentic_dev.tracks import Track
from agentic_dev.uat.dispatcher import pick_uat_agent


_event_log = get_event_logger("uat.prereqs")

_PROBE_TIMEOUT_SECONDS = 10


@dataclass
class PrereqProbe:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PrereqResult:
    agent_name: str
    probes: list[PrereqProbe]
    missing: list[str]
    ok: bool
    uat_mode: str = "full"
    notes: list[str] = field(default_factory=list)


def check_prereqs(
    track: Track,
    desktop_framework: str | None = None,
    project_dir=None,
) -> PrereqResult:
    """Probe the environment for the UAT agent that would run for ``track``."""
    agent_name = pick_uat_agent(track, desktop_framework)
    env = discover_mcp_servers(project_dir=project_dir)

    probes: list[PrereqProbe] = []
    missing: list[str] = []

    if agent_name == "uat_web":
        probes.extend(_probes_web(env))
    elif agent_name == "uat_cli":
        pass  # Bash suffices; no extra probes.
    elif agent_name == "uat_desktop_electron":
        probes.extend(_probes_web(env))
    elif agent_name == "uat_desktop_tauri":
        probes.extend(_probes_tauri())
    elif agent_name == "uat_mobile":
        probes.extend(_probes_mobile())
    elif agent_name == "uat_api":
        probes.extend(_probes_api())

    for probe in probes:
        if not probe.ok:
            missing.append(f"{probe.name}: {probe.detail}" if probe.detail else probe.name)

    ok = not missing

    if missing:
        emit(_event_log, UATPrereqValidationEvent(
            agent_name=agent_name,
            missing=missing,
            message=f"UAT prereq validation for {agent_name}: {len(missing)} missing",
        ))

    return PrereqResult(
        agent_name=agent_name,
        probes=probes,
        missing=missing,
        ok=ok,
    )


def render_doc(result: PrereqResult) -> str:
    """Format a PrereqResult as the ``uat_prereqs`` document the UAT agent reads."""
    lines = [
        "# UAT Prereqs",
        "",
        f"**Agent:** {result.agent_name}",
        f"**Overall:** {'PASS' if result.ok else 'FAIL — driver unavailable'}",
        "",
        "## Probes",
        "",
    ]
    if not result.probes:
        lines.append("- (no runtime probes required for this agent)")
    for probe in result.probes:
        status = "OK" if probe.ok else "MISSING"
        tail = f" — {probe.detail}" if probe.detail else ""
        lines.append(f"- **{probe.name}:** {status}{tail}")
    if result.missing:
        lines.extend([
            "",
            "## Missing",
            "",
        ])
        for item in result.missing:
            lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


# -- Individual probe helpers -------------------------------------------------

def _probes_web(env: ClaudeMCPEnvironment) -> list[PrereqProbe]:
    return [
        _probe_mcp_server(env, "playwright"),
        _probe_command("npx", ["npx", "--version"]),
    ]


def _probes_tauri() -> list[PrereqProbe]:
    return [_probe_command("tauri-driver", ["tauri-driver", "--version"])]


def _probes_mobile() -> list[PrereqProbe]:
    """Mobile passes if either Maestro+doctor OR Flutter+non-web-device is ready.

    Returns only the winning path's probes when one path succeeds so the
    other path's absence doesn't force a misleading missing-prereq flag.
    When neither path is ready, both are reported so the user sees what's
    required.
    """
    maestro_binary = _probe_command("maestro", ["maestro", "--version"])
    maestro_doctor = (
        _probe_command("maestro doctor", ["maestro", "doctor"])
        if maestro_binary.ok
        else PrereqProbe(name="maestro doctor", ok=False, detail="maestro binary missing")
    )
    if maestro_binary.ok and maestro_doctor.ok:
        return [maestro_binary, maestro_doctor]

    flutter_binary = _probe_command("flutter", ["flutter", "--version"])
    flutter_devices = (
        _probe_flutter_devices() if flutter_binary.ok
        else PrereqProbe(name="flutter devices", ok=False, detail="flutter binary missing")
    )
    if flutter_binary.ok and flutter_devices.ok:
        return [flutter_binary, flutter_devices]

    return [maestro_binary, maestro_doctor, flutter_binary, flutter_devices]


def _probes_api() -> list[PrereqProbe]:
    return [_probe_command("curl", ["curl", "--version"])]


def _probe_mcp_server(env: ClaudeMCPEnvironment, name: str) -> PrereqProbe:
    entry = find_server_for_service(env, name)
    if entry is None:
        return PrereqProbe(
            name=f"mcp:{name}",
            ok=False,
            detail="not registered in Claude Code settings or plugins",
        )
    return PrereqProbe(name=f"mcp:{name}", ok=True, detail=entry.source)


def _probe_command(display_name: str, cmd: list[str]) -> PrereqProbe:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return PrereqProbe(name=display_name, ok=False, detail="not on PATH")
    except subprocess.TimeoutExpired:
        return PrereqProbe(name=display_name, ok=False, detail="probe timed out")
    if proc.returncode == 0:
        first_line = (proc.stdout.splitlines() or [""])[0].strip() or "ok"
        return PrereqProbe(name=display_name, ok=True, detail=first_line)
    return PrereqProbe(
        name=display_name, ok=False, detail=(proc.stderr or proc.stdout).strip()[:120]
    )


def _probe_flutter_devices() -> PrereqProbe:
    """Flutter has a non-web device if `flutter devices` lists anything non-web."""
    try:
        proc = subprocess.run(
            ["flutter", "devices"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return PrereqProbe(name="flutter devices", ok=False, detail="flutter not on PATH")
    except subprocess.TimeoutExpired:
        return PrereqProbe(name="flutter devices", ok=False, detail="probe timed out")

    if proc.returncode != 0:
        return PrereqProbe(
            name="flutter devices",
            ok=False,
            detail=(proc.stderr or proc.stdout).strip()[:120],
        )

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    # Heuristic: any device whose line doesn't mention "web" counts as non-web.
    non_web = [line for line in lines if "web" not in line.lower()]
    if not non_web:
        return PrereqProbe(
            name="flutter devices",
            ok=False,
            detail="no non-web device/simulator available",
        )
    return PrereqProbe(name="flutter devices", ok=True, detail=non_web[0].strip()[:80])
