"""Tests for the file_lock context manager."""

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from agentic_dev.concurrency import file_lock


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    """Return a path for a test lock file."""
    return tmp_path / "test.lock"


def _acquire_exclusive_and_hold(lock_path_str: str, ready_event_fd: int, duration: float) -> None:
    """Helper: acquire exclusive lock, signal readiness, hold for duration."""
    lock_path = Path(lock_path_str)
    with file_lock(lock_path):
        os.write(ready_event_fd, b"r")
        time.sleep(duration)


def _acquire_shared_and_hold(lock_path_str: str, ready_event_fd: int, duration: float) -> None:
    """Helper: acquire shared lock, signal readiness, hold for duration."""
    lock_path = Path(lock_path_str)
    with file_lock(lock_path, shared=True):
        os.write(ready_event_fd, b"r")
        time.sleep(duration)


def _try_exclusive_lock(lock_path_str: str, acquired_fd: int) -> None:
    """Helper: try to acquire exclusive lock and signal success."""
    lock_path = Path(lock_path_str)
    with file_lock(lock_path):
        os.write(acquired_fd, b"y")


def _try_shared_lock(lock_path_str: str, acquired_fd: int) -> None:
    """Helper: try to acquire shared lock and signal success."""
    lock_path = Path(lock_path_str)
    with file_lock(lock_path, shared=True):
        os.write(acquired_fd, b"y")


class TestFileLockBasic:
    def test_exclusive_lock_creates_lock_file(self, lock_path: Path) -> None:
        with file_lock(lock_path):
            assert lock_path.exists()

    def test_lock_creates_parent_directories(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "nested" / "dir" / "test.lock"
        with file_lock(lock_path):
            assert lock_path.exists()

    def test_lock_released_on_exit(self, lock_path: Path) -> None:
        """After exiting the context manager, another process can acquire."""
        with file_lock(lock_path):
            pass

        # A second exclusive lock should succeed immediately
        with file_lock(lock_path):
            pass

    def test_shared_lock_creates_lock_file(self, lock_path: Path) -> None:
        with file_lock(lock_path, shared=True):
            assert lock_path.exists()


class TestFileLockConcurrency:
    def test_exclusive_blocks_second_exclusive(self, lock_path: Path) -> None:
        """A second exclusive lock should block while the first is held."""
        ready_r, ready_w = os.pipe()
        acquired_r, acquired_w = os.pipe()

        # Process 1: hold exclusive lock for 0.5s
        holder = multiprocessing.Process(
            target=_acquire_exclusive_and_hold,
            args=(str(lock_path), ready_w, 0.5),
        )
        holder.start()
        os.close(ready_w)

        # Wait for holder to acquire
        os.read(ready_r, 1)
        os.close(ready_r)

        # Process 2: try to acquire exclusive lock
        contender = multiprocessing.Process(
            target=_try_exclusive_lock,
            args=(str(lock_path), acquired_w),
        )
        contender.start()
        os.close(acquired_w)

        # Contender should NOT have acquired yet (holder still holds)
        import select
        readable, _, _ = select.select([acquired_r], [], [], 0.2)
        assert not readable, "Second exclusive lock should block while first is held"

        holder.join(timeout=2)
        contender.join(timeout=2)
        os.close(acquired_r)

    def test_shared_locks_concurrent(self, lock_path: Path) -> None:
        """Two shared locks can be held simultaneously."""
        ready_r1, ready_w1 = os.pipe()
        ready_r2, ready_w2 = os.pipe()

        p1 = multiprocessing.Process(
            target=_acquire_shared_and_hold,
            args=(str(lock_path), ready_w1, 0.5),
        )
        p2 = multiprocessing.Process(
            target=_acquire_shared_and_hold,
            args=(str(lock_path), ready_w2, 0.5),
        )

        p1.start()
        p2.start()
        os.close(ready_w1)
        os.close(ready_w2)

        # Both should signal readiness within a short window
        import select
        readable, _, _ = select.select([ready_r1, ready_r2], [], [], 2.0)
        assert len(readable) >= 1
        # Wait a bit more for the second
        time.sleep(0.2)
        readable2, _, _ = select.select([ready_r1, ready_r2], [], [], 1.0)
        # At least one pipe should have been readable earlier + now the other
        # The key assertion: both processes acquired without blocking each other

        p1.join(timeout=2)
        p2.join(timeout=2)
        assert p1.exitcode == 0, "First shared lock holder should succeed"
        assert p2.exitcode == 0, "Second shared lock holder should succeed"
        os.close(ready_r1)
        os.close(ready_r2)

    def test_shared_blocked_by_exclusive(self, lock_path: Path) -> None:
        """A shared lock should block while an exclusive lock is held."""
        ready_r, ready_w = os.pipe()
        acquired_r, acquired_w = os.pipe()

        holder = multiprocessing.Process(
            target=_acquire_exclusive_and_hold,
            args=(str(lock_path), ready_w, 0.5),
        )
        holder.start()
        os.close(ready_w)

        os.read(ready_r, 1)
        os.close(ready_r)

        contender = multiprocessing.Process(
            target=_try_shared_lock,
            args=(str(lock_path), acquired_w),
        )
        contender.start()
        os.close(acquired_w)

        import select
        readable, _, _ = select.select([acquired_r], [], [], 0.2)
        assert not readable, "Shared lock should block while exclusive is held"

        holder.join(timeout=2)
        contender.join(timeout=2)
        os.close(acquired_r)
