"""Process-scoped exclusion for a durable repair identity.

The OS releases the lock after process death. The persisted run, stage outputs
and budget survive; a retry resumes that same identity.
"""

import os
from contextlib import contextmanager
from pathlib import Path

from src.memory.collaboration.repository import CoordinationConflict


class RepairBusy(CoordinationConflict):
    pass


@contextmanager
def repair_lock(db_path: str, repair_id: str):
    directory = Path(db_path).parent / "repair-locks"
    directory.mkdir(exist_ok=True)
    path = directory / (repair_id + ".lock")
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RepairBusy("repair is already running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
