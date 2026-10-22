"""Globally unique IDs in the codebook formats, persisted across program restarts.

The registry is a small JSON file next to the outputs, guarded by an exclusive file lock, so two
runner processes (or a restart after a crash) never issue the same run_id / response_id / batch id.
Threads of one process (parallel runner) first serialize on an in-process lock per registry file, so they
never contend for the flock itself — flock contention on NFS stalls badly (STRUCTURE_REVIEW C11).
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def thread_lock_for(path: Path) -> threading.Lock:
    """One in-process lock per file (shared by every object that writes that file)."""
    key = str(Path(path).resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


class IdRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tlock = thread_lock_for(self.path)

    @contextmanager
    def _locked_state(self):
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with self._tlock, open(lock, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield state
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
            os.replace(tmp, self.path)

    def _next(self, key: str) -> int:
        with self._locked_state() as s:
            s[key] = s.get(key, 0) + 1
            return s[key]

    def new_run_batch_id(self, now: datetime | None = None) -> str:
        d = (now or datetime.now()).strftime("%Y%m%d")
        n = self._next(f"rbatch:{d}")
        if n > 999:
            raise RuntimeError(f"run_batch_id 일일 한도(999) 초과: {d}")
        return f"RBATCH-{d}-{n:03d}"

    def new_run_id(self, now: datetime | None = None) -> str:
        d = (now or datetime.now()).strftime("%Y%m%d")
        n = self._next(f"run:{d}")
        if n > 999_999:
            raise RuntimeError(f"run_id 일일 한도 초과: {d}")
        return f"RUN-{d}-{n:06d}"

    def new_response_id(self) -> str:
        return f"RESP-{self._next('resp'):08d}"
