"""CSV I/O with the exact codebook column order.

All cells are read and written as strings (UTF-8, RFC-4180 quoting), so JSON and multi-line text
survive a round trip byte-for-byte. Writes are durable: every append is flushed and fsync'ed before
the runner moves on, and row updates rewrite the file atomically (temp file + rename).
"""
from __future__ import annotations

import csv
import fcntl
import os
from pathlib import Path

from .ids import thread_lock_for
from .schemas import Table

csv.field_size_limit(1 << 30)


def read_table(path: str | Path, table: Table, *, allow_extra: bool = True) -> list[dict[str, str]]:
    """Read a CSV, requiring every codebook column. Unknown extra columns are kept, not dropped.

    utf-8-sig: a UTF-8 BOM (Excel "CSV UTF-8") is stripped instead of ending up in the first header name.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in table.columns if c not in header]
        if missing:
            raise ValueError(f"{path}: 코드북 {table.sheet} 컬럼 누락 {missing}")
        extra = [c for c in header if c not in table.columns]
        if extra and not allow_extra:
            raise ValueError(f"{path}: 코드북에 없는 컬럼 {extra}")
        return [{k: (v if v is not None else "") for k, v in row.items()} for row in reader]


def write_table(path: str | Path, table: Table, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=table.columns, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r.get(c)) for c in table.columns})
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


class TableWriter:
    """Append-only writer for runs.csv / responses.csv that also supports updating a row by key."""

    def __init__(self, path: str | Path, table: Table):
        self.path = Path(path)
        self.table = table
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tlock = thread_lock_for(self.path)  # threads of the parallel runner: in-process first, then flock
        if not self.path.exists() or self.path.stat().st_size == 0:
            write_table(self.path, table, [])
        else:
            with open(self.path, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), [])
            if header != table.columns:
                raise ValueError(f"{self.path}: 기존 파일 헤더가 코드북 {table.sheet}와 다름")

    def _lock(self):
        self._tlock.acquire()
        try:
            lf = open(self.path.with_suffix(self.path.suffix + ".lock"), "a+")
            fcntl.flock(lf, fcntl.LOCK_EX)
        except BaseException:
            self._tlock.release()
            raise
        return _Held(lf, self._tlock)

    def append(self, row: dict) -> None:
        lf = self._lock()
        try:
            with open(self.path, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.table.columns, extrasaction="raise").writerow(
                    {c: _cell(row.get(c)) for c in self.table.columns})
                f.flush()
                os.fsync(f.fileno())
        finally:
            lf.close()

    def upsert(self, row: dict) -> None:
        """Replace the row with the same primary key (or append it) via an atomic rewrite."""
        lf = self._lock()
        try:
            rows = read_table(self.path, self.table, allow_extra=False)
            key = tuple(_cell(row.get(k)) for k in self.table.primary_key)
            out, found = [], False
            for r in rows:
                if tuple(r[k] for k in self.table.primary_key) == key:
                    out.append(row)
                    found = True
                else:
                    out.append(r)
            if not found:
                out.append(row)
            write_table(self.path, self.table, out)
        finally:
            lf.close()


class _Held:
    """flock + in-process lock; close() releases both (callers already use try/finally: lf.close())."""

    def __init__(self, lf, tlock):
        self._lf, self._tlock = lf, tlock

    def close(self) -> None:
        try:
            self._lf.close()
        finally:
            self._tlock.release()
