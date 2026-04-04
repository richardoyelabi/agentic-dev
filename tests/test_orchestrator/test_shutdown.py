"""Tests for the shutdown module: event management and signal handling."""

import asyncio
import signal

import pytest

from agentic_dev.orchestrator.shutdown import get_shutdown_event, install_signal_handlers


class TestGetShutdownEvent:
    """Tests for get_shutdown_event singleton."""

    def test_returns_asyncio_event(self):
        event = get_shutdown_event()
        assert isinstance(event, asyncio.Event)

    def test_returns_same_instance(self):
        event1 = get_shutdown_event()
        event2 = get_shutdown_event()
        assert event1 is event2

    def test_event_not_set_initially(self):
        event = get_shutdown_event()
        assert not event.is_set()


class TestInstallSignalHandlers:
    """Tests for install_signal_handlers within a running event loop."""

    @pytest.mark.asyncio
    async def test_installs_without_error(self):
        install_signal_handlers()

    @pytest.mark.asyncio
    async def test_setting_event_is_detectable(self):
        install_signal_handlers()
        event = get_shutdown_event()
        event.set()
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_sigint_handler_registered(self):
        install_signal_handlers()
        loop = asyncio.get_running_loop()
        # Sending SIGINT to ourselves should set the shutdown event
        event = get_shutdown_event()
        event.clear()
        loop.call_soon(lambda: signal.raise_signal(signal.SIGINT))
        await asyncio.sleep(0.05)
        assert event.is_set()

    @pytest.mark.asyncio
    async def test_sigterm_handler_registered(self):
        install_signal_handlers()
        event = get_shutdown_event()
        event.clear()
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: signal.raise_signal(signal.SIGTERM))
        await asyncio.sleep(0.05)
        assert event.is_set()
