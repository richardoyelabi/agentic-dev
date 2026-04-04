"""File-based locking for concurrent access protection using fcntl.flock."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(lock_path: Path, *, shared: bool = False) -> Iterator[None]:
    """Acquire a file lock, yielding control while the lock is held.

    Uses fcntl.flock which auto-releases on process crash (kernel cleanup).
    Lock files persist on disk but locks are kernel-managed, not
    file-presence-managed — no stale lock problem.

    Args:
        lock_path: Path to the lock file (created if it doesn't exist).
        shared: If True, acquire a shared (read) lock. Otherwise exclusive (write).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
