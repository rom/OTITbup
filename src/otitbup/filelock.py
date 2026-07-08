"""Cross-process advisory file lock.

GitStore's in-process lock does not protect against a cron-driven
`otitbup backup` racing the `otitbup daemon` — two processes writing the
same git repo can corrupt an index. This wraps a POSIX flock (with a
best-effort fallback where flock is unavailable) so the backup/retention
critical section is single-writer across processes.

Non-blocking by default: if another process holds the lock, acquisition
raises LockBusy rather than queueing, so a scheduled run skips cleanly
instead of piling up.
"""
from __future__ import annotations

import os
from pathlib import Path


class LockBusy(Exception):
    pass


class FileLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self, blocking: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            import fcntl
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, flags)
            except OSError as exc:
                os.close(fd)
                raise LockBusy(
                    f"another otitbup process holds {self.path}"
                ) from exc
        except ImportError:
            # No fcntl (non-POSIX): fall back to atomic O_EXCL create.
            os.close(fd)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError as exc:
                raise LockBusy(
                    f"lock file {self.path} exists (another process?)"
                ) from exc
            self._exclusive_created = True
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except ImportError:
            if getattr(self, "_exclusive_created", False):
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
