"""Graceful shutdown support: event management and signal handler installation."""

import asyncio
import signal

_shutdown_event: asyncio.Event | None = None


def get_shutdown_event() -> asyncio.Event:
    """Return the singleton shutdown event, creating it if needed."""
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def install_signal_handlers() -> None:
    """Install SIGINT/SIGTERM handlers on the running event loop.

    Must be called from within an async context (i.e., inside a running loop).
    The handlers set the shutdown event so the pipeline can exit gracefully.
    """
    loop = asyncio.get_running_loop()
    event = get_shutdown_event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, event.set)
