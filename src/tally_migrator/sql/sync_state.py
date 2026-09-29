"""Synchronisation state management in SQL Server.

The migration_sync_state table in SQL Server tracks the state
of each synchronisation run per collection. This ensures:

1. Failed syncs are marked as failed (not success).
2. Successful syncs record enough state for incremental sync.
3. Each collection can be resumed or retried independently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from tally_migrator.sql.connection import SQLConnection

logger = logging.getLogger(__name__)


@dataclass
class SyncState:
    collection_name: str
    last_successful_sync: Optional[datetime]
    source_marker: Optional[str]
    last_run_id: Optional[str]
    status: str
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_deleted: int
    rows_failed: int
    error_message: Optional[str]


class SyncStateManager:
    """Manages synchronisation state in SQL Server."""

    TABLE = "migration_sync_state"

    def __init__(self, sql: SQLConnection, config):
        self.sql = sql
        self.config = config
        self._schema = config.sql.schema_name
        self._ensure_table()

    def _table_ref(self) -> str:
        return f"[{self._schema}].[{self.TABLE}]"

    def _ensure_table(self) -> None:
        """Create the sync state table if it doesn't exist."""
        ddl = f"""
        IF NOT EXISTS (
            SELECT 1 FROM sys.objects
            WHERE object_id = OBJECT_ID(N'[{self._schema}].[{self.TABLE}]')
            AND type = 'U'
        )
        BEGIN
            CREATE TABLE [{self._schema}].[{self.TABLE}] (
                [id]                    INT IDENTITY(1,1)   NOT NULL,
                [collection_name]       NVARCHAR(100)       NOT NULL,
                [last_successful_sync]  DATETIME2           NULL,
                [source_marker]         NVARCHAR(255)       NULL,
                [last_run_id]           NVARCHAR(50)        NULL,
                [status]                NVARCHAR(20)        NOT NULL DEFAULT 'pending',
                [rows_read]             BIGINT              NOT NULL DEFAULT 0,
                [rows_inserted]         BIGINT              NOT NULL DEFAULT 0,
                [rows_updated]          BIGINT              NOT NULL DEFAULT 0,
                [rows_deleted]          BIGINT              NOT NULL DEFAULT 0,
                [rows_failed]           BIGINT              NOT NULL DEFAULT 0,
                [error_message]         NVARCHAR(MAX)       NULL,
                [created_at]            DATETIME2           NOT NULL DEFAULT SYSUTCDATETIME(),
                [updated_at]            DATETIME2           NOT NULL DEFAULT SYSUTCDATETIME(),
                CONSTRAINT [PK_{self.TABLE}] PRIMARY KEY ([id]),
                CONSTRAINT [UQ_{self.TABLE}_collection] UNIQUE ([collection_name])
            );
        END
        """
        try:
            cursor = self.sql.cursor()
            cursor.execute(ddl)
            self.sql.commit()
            cursor.close()
        except Exception as exc:
            logger.warning("Could not create sync state table: %s", exc)

    def get_state(self, collection_name: str) -> Optional[SyncState]:
        """Get current sync state for a collection."""
        try:
            cursor = self.sql.cursor()
            cursor.execute(
                f"SELECT collection_name, last_successful_sync, source_marker, "
                f"last_run_id, status, rows_read, rows_inserted, rows_updated, "
                f"rows_deleted, rows_failed, error_message "
                f"FROM {self._table_ref()} WHERE collection_name = ?",
                (collection_name,),
            )
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            return SyncState(
                collection_name=row[0],
                last_successful_sync=row[1],
                source_marker=row[2],
                last_run_id=row[3],
                status=row[4] or "unknown",
                rows_read=row[5] or 0,
                rows_inserted=row[6] or 0,
                rows_updated=row[7] or 0,
                rows_deleted=row[8] or 0,
                rows_failed=row[9] or 0,
                error_message=row[10],
            )
        except Exception as exc:
            logger.error("Cannot read sync state for %s: %s", collection_name, exc)
            return None

    def get_all_states(self) -> list[SyncState]:
        """Return sync state for all known collections."""
        try:
            cursor = self.sql.cursor()
            cursor.execute(
                f"SELECT collection_name, last_successful_sync, source_marker, "
                f"last_run_id, status, rows_read, rows_inserted, rows_updated, "
                f"rows_deleted, rows_failed, error_message "
                f"FROM {self._table_ref()} ORDER BY collection_name"
            )
            rows = cursor.fetchall()
            cursor.close()
            return [
                SyncState(
                    collection_name=r[0], last_successful_sync=r[1],
                    source_marker=r[2], last_run_id=r[3],
                    status=r[4] or "unknown",
                    rows_read=r[5] or 0, rows_inserted=r[6] or 0,
                    rows_updated=r[7] or 0, rows_deleted=r[8] or 0,
                    rows_failed=r[9] or 0, error_message=r[10],
                )
                for r in rows
            ]
        except Exception as exc:
            logger.error("Cannot read all sync states: %s", exc)
            return []

    def mark_running(self, collection_name: str, run_id: str) -> None:
        """Mark a collection as currently being synced."""
        self._upsert_status(collection_name, "running", run_id=run_id, clear_error=True)

    def mark_success(
        self,
        collection_name: str,
        run_id: str,
        source_marker: Optional[str],
        rows_read: int,
        rows_inserted: int,
        rows_updated: int,
        rows_deleted: int,
        rows_failed: int,
    ) -> None:
        """Mark collection sync as successful with final statistics."""
        now = datetime.now(timezone.utc)
        try:
            cursor = self.sql.cursor()
            cursor.execute(
                f"UPDATE {self._table_ref()} SET "
                f"last_successful_sync=?, source_marker=?, last_run_id=?, "
                f"status='success', rows_read=?, rows_inserted=?, rows_updated=?, "
                f"rows_deleted=?, rows_failed=?, error_message=NULL, updated_at=SYSUTCDATETIME() "
                f"WHERE collection_name=?",
                (now, source_marker, run_id, rows_read, rows_inserted,
                 rows_updated, rows_deleted, rows_failed, collection_name),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    f"INSERT INTO {self._table_ref()} "
                    f"(collection_name, last_successful_sync, source_marker, last_run_id, "
                    f"status, rows_read, rows_inserted, rows_updated, rows_deleted, rows_failed) "
                    f"VALUES (?, ?, ?, ?, 'success', ?, ?, ?, ?, ?)",
                    (collection_name, now, source_marker, run_id,
                     rows_read, rows_inserted, rows_updated, rows_deleted, rows_failed),
                )
            self.sql.commit()
            cursor.close()
        except Exception as exc:
            logger.error("Cannot mark sync success for %s: %s", collection_name, exc)

    def mark_failed(
        self,
        collection_name: str,
        run_id: str,
        error: str,
        rows_read: int = 0,
        rows_inserted: int = 0,
        rows_failed: int = 0,
    ) -> None:
        """Mark collection sync as failed."""
        try:
            cursor = self.sql.cursor()
            # Truncate error message
            error_msg = str(error)[:4000]
            cursor.execute(
                f"UPDATE {self._table_ref()} SET "
                f"last_run_id=?, status='failed', rows_read=?, rows_inserted=?, "
                f"rows_failed=?, error_message=?, updated_at=SYSUTCDATETIME() "
                f"WHERE collection_name=?",
                (run_id, rows_read, rows_inserted, rows_failed, error_msg, collection_name),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    f"INSERT INTO {self._table_ref()} "
                    f"(collection_name, last_run_id, status, rows_read, rows_inserted, "
                    f"rows_failed, error_message) "
                    f"VALUES (?, ?, 'failed', ?, ?, ?, ?)",
                    (collection_name, run_id, rows_read, rows_inserted, rows_failed, error_msg),
                )
            self.sql.commit()
            cursor.close()
        except Exception as exc:
            logger.error("Cannot mark sync failed for %s: %s", collection_name, exc)

    def _upsert_status(self, collection_name: str, status: str, run_id: str = "", clear_error: bool = False) -> None:
        try:
            cursor = self.sql.cursor()
            cursor.execute(
                f"UPDATE {self._table_ref()} SET status=?, last_run_id=?, updated_at=SYSUTCDATETIME()"
                + (" ,error_message=NULL" if clear_error else "")
                + " WHERE collection_name=?",
                (status, run_id, collection_name),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    f"INSERT INTO {self._table_ref()} (collection_name, status, last_run_id) VALUES (?, ?, ?)",
                    (collection_name, status, run_id),
                )
            self.sql.commit()
            cursor.close()
        except Exception as exc:
            logger.error("Cannot upsert status for %s: %s", collection_name, exc)
