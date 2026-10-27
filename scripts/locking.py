# Copyright 2026 Adam Gogacz
# SPDX-License-Identifier: Apache-2.0

"""Bound short filesystem locks used for project pairing and session state."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from settings import Settings, require

if TYPE_CHECKING:
    from delivery import Clock


@contextmanager
def file_lock(path: Path, clock: Clock | None = None) -> Iterator[None]:
    """Serialize a short operation; a lock never represents agent liveness."""
    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        require(
            stat.S_ISREG(os.fstat(descriptor).st_mode), "lock must be a regular file"
        )
        monotonic = clock.monotonic if clock is not None else time.monotonic
        sleep = clock.sleep if clock is not None else time.sleep
        stop = monotonic() + Settings.default("wait_timeout_seconds")
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                remaining = stop - monotonic()
                require(remaining > 0, "coordination is busy; retry the same operation")
                sleep(min(Settings.default("lock_poll_interval_seconds"), remaining))
        yield
    finally:
        os.close(descriptor)
