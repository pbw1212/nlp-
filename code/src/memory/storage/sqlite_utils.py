from __future__ import annotations

import sqlite3
from types import TracebackType
from typing import Literal, Optional, Type


class ClosingSQLiteConnection(sqlite3.Connection):
    """SQLite connection whose context manager also releases the file handle."""

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc_value, traceback)
            return False
        finally:
            self.close()
