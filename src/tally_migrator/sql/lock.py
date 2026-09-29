"""Process lock management using SQL Server.

Prevents concurrent synchronisation runs from executing simultaneously.
Uses SQL Server as the lock store (no external lock file needed).

This is essential for Windows Task Scheduler where multiple triggers
could overlap.
"""

from __future__ import annotations

import logging
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from tally_migrator.exceptions import LockError
from tally_migrator.sql.connection import SQLConnection

logger = logging.getLogger(__name__)


class SQLProcessLock:
    """Distributed process lock using SQL Server.

    Ensures only one migration process runs at a time.
    Lock is stored in the migration_lock table.
    Lock is automatically released on process exit via context manager.

    Stale lock detection: if a lock is older than max_lock_age_minutes,
    it is considered stale and can be overridden.
    """

    TABLE = "migration_lock"
    DEFAULT_LOCK_NAME = "sync_process"
    DEFAULT_MAX_AGE_MINUTES = 120

    def __init__(
        self,
        sql: SQLConnection,
        config,
        lock_name: str = DEFAULT_LOCK_NAME,
        max_lock_age_minutes: int = DEFAULT_MAX_AGE_MINUTES,
    ):
        self.sql = sql
        self.config = config
        self.lock_name = lock_name
        self.max_lock_age_minutes = max_lock_age_minutes
        self._schema = config.sql.schema_name
        self._lock_holder: Optional[str] = None
        self._ensure_table()

    def _table_ref(self) -> str:
        return f"[{self._schema}].[{self.TABLE}]"

    def _ensure_table(self) -> None:
        ddl = f"""
        IF NOT EXISTS (
            SELECT 1 FROM sys.objects
            WHERE object_id = OBJECT_ID(N'[{self._schema}].[{self.TABLE}]')
            AND type = 'U'
        )
        BEGIN
            CREATE TABLE [{self._schema}].[{self.TABLE}] (
                [lock_name]     NVARCHAR(100)   NOT NULL,
                [locked_at]     DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME(),
                [locked_by]     NVARCHAR(255)   NULL,
                [run_id]        NVARCHAR(50)    NULL,
                CONSTRAINT [PK_{self.TABLE}] PRIMARY KEY ([lock_name])
            );
        END
        """
        try:
            cursor = self.sql.cursor()
            cursor.execute(ddl)
            self.sql.commit()
            cursor.close()
        except Exception as exc:
            logger.warning("Could not create lock table: %s", exc)

    def _lock_info(self) -> str:
        return f"{socket.gethostname()}:{os.getpid()}"

    def acquire(self, run_id: str = "", force: bool = False) -> None:
        """Acquire the process lock.

        Raises:
            LockError: If lock is already held by another process.
        """
        holder = self._lock_info()
        now = datetime.now(timezone.utc)

        cursor = self.sql.cursor()
        try:
            # Check for existing lock
            cursor.execute(
                f"SELECT locked_at, locked_by, run_id FROM {self._table_ref()} "
                f"WHERE lock_name = ?",
                (self.lock_name,),
            )
            row = cursor.fetchone()

            if row:
                locked_at, locked_by, existing_run_id = row
                # Check if lock is stale
                if locked_at:
                    age_minutes = (now - locked_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
                    if age_minutes > self.max_lock_age_minutes:
                        logger.warning(
                            "Stale lock detected (age=%.0f min, holder=%s). Overriding.",
                            age_minutes, locked_by
                        )
                        force = True

                if not force:
                    cursor.close()
                    raise LockError(
                        f"held by {locked_by} since {locked_at} (run_id={existing_run_id})"
                    )
                else:
                    # Override stale lock
                    cursor.execute(
                        f"UPDATE {self._table_ref()} SET locked_at=?, locked_by=?, run_id=? "
                        f"WHERE lock_name=?",
                        (now, holder, run_id, self.lock_name),
                    )
            else:
                cursor.execute(
                    f"INSERT INTO {self._table_ref()} (lock_name, locked_at, locked_by, run_id) "
                    f"VALUES (?, ?, ?, ?)",
                    (self.lock_name, now, holder, run_id),
                )

            self.sql.commit()
            self._lock_holder = holder
            logger.debug("Lock '%s' acquired by %s", self.lock_name, holder)
        except LockError:
            cursor.close()
            raise
        except Exception as exc:
            cursor.close()
            logger.warning("Lock acquire failed (non-fatal): %s", exc)

    def release(self) -> None:
        """Release the process lock."""
        try:
            cursor = self.sql.cursor()
            cursor.execute(
                f"DELETE FROM {self._table_ref()} WHERE lock_name = ?",
                (self.lock_name,),
            )
            self.sql.commit()
            cursor.close()
            logger.debug("Lock '%s' released.", self.lock_name)
            self._lock_holder = None
        except Exception as exc:
            logger.warning("Lock release failed: %s", exc)

    @contextmanager
    def held(self, run_id: str = "") -> Iterator[None]:
        """Context manager that acquires lock on enter, releases on exit."""
        self.acquire(run_id=run_id)
        try:
            yield
        finally:
            self.release()
