"""Unit tests for SQL Server layer components using mocks."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tally_migrator.config import SQLConfig
from tally_migrator.exceptions import LockError
from tally_migrator.sql.lock import SQLProcessLock
from tally_migrator.sql.sync_state import SyncStateManager
from tally_migrator.sql.upsert import BatchUpsertEngine, UpsertResult


@pytest.fixture
def sql_config():
    return SQLConfig(
        server="localhost",
        database="TallyDBTest",
        trusted_connection=True,
    )


class TestBatchUpsertEngine:
    def test_upsert_result_add(self):
        r1 = UpsertResult(inserted=10, updated=2, unchanged=3, failed=1)
        r2 = UpsertResult(inserted=5, updated=4, unchanged=1, failed=0)
        tot = r1 + r2
        assert tot.inserted == 15
        assert tot.updated == 6
        assert tot.unchanged == 4
        assert tot.failed == 1

    def test_upsert_batch_dry_run(self, sql_config):
        mock_sql = MagicMock()
        engine = BatchUpsertEngine(mock_sql, "dbo", "ledger", "guid", run_id="run-1")
        records = [
            {"guid": "g1", "name": "Cash"},
            {"guid": "g2", "name": "Bank"},
        ]
        res = engine.upsert_batch(records, dry_run=True)
        assert res.inserted == 2
        assert res.failed == 0
        mock_sql.cursor.assert_not_called()

    def test_upsert_batch_missing_key_skipped(self, sql_config):
        mock_sql = MagicMock()
        engine = BatchUpsertEngine(mock_sql, "dbo", "ledger", "guid", run_id="run-1")
        records = [
            {"guid": "g1", "name": "Cash"},
            {"name": "NoGuid"},  # missing key
        ]
        res = engine.upsert_batch(records, dry_run=True)
        assert res.inserted == 1
        assert res.failed == 1

    def test_upsert_batch_executes_merge(self, sql_config):
        mock_sql = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("INSERT",)
        mock_sql.cursor.return_value = mock_cursor

        engine = BatchUpsertEngine(mock_sql, "dbo", "ledger", "guid", run_id="run-1")
        records = [{"guid": "g1", "name": "Cash"}]
        res = engine.upsert_batch(records, dry_run=False)
        assert res.inserted == 1
        assert mock_cursor.execute.called

    def test_change_check_includes_all_columns(self):
        mock_sql = MagicMock()
        engine = BatchUpsertEngine(mock_sql, "dbo", "ledger", "guid")
        cols = [f"col_{i}" for i in range(25)]
        check_sql = engine._build_change_check(cols)
        for c in cols:
            assert f"tgt.[{c}]" in check_sql


class TestSyncStateManager:
    def test_get_state_not_found(self, sql_config):
        mock_sql = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_sql.cursor.return_value = mock_cursor

        cfg = MagicMock()
        cfg.sql = sql_config
        mgr = SyncStateManager(mock_sql, cfg)
        state = mgr.get_state("Ledger")
        assert state is None

    def test_get_state_found(self, sql_config):
        mock_sql = MagicMock()
        mock_cursor = MagicMock()
        # Columns: collection_name, last_successful_sync, source_marker, last_run_id, status, rows_read, rows_inserted, rows_updated, rows_deleted, rows_failed, error_message
        mock_cursor.fetchone.return_value = (
            "Ledger", None, "100", "run-123", "completed", 10, 8, 2, 0, 0, None
        )
        mock_sql.cursor.return_value = mock_cursor

        cfg = MagicMock()
        cfg.sql = sql_config
        mgr = SyncStateManager(mock_sql, cfg)
        state = mgr.get_state("Ledger")
        assert state is not None
        assert state.collection_name == "Ledger"
        assert state.source_marker == "100"
        assert state.status == "completed"


class TestSQLProcessLock:
    def test_acquire_lock_success(self, sql_config):
        mock_sql = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # no existing lock
        mock_sql.cursor.return_value = mock_cursor

        cfg = MagicMock()
        cfg.sql = sql_config
        lock = SQLProcessLock(mock_sql, cfg)
        lock.acquire(run_id="run-99")
        assert lock._lock_holder is not None

    def test_acquire_lock_already_locked(self, sql_config):
        mock_sql = MagicMock()
        mock_cursor = MagicMock()
        from datetime import datetime, timezone
        mock_cursor.fetchone.return_value = (
            datetime.now(timezone.utc), "another_host:1234", "run-00"
        )
        mock_sql.cursor.return_value = mock_cursor

        cfg = MagicMock()
        cfg.sql = sql_config
        lock = SQLProcessLock(mock_sql, cfg)
        with pytest.raises(LockError):
            lock.acquire(run_id="run-99")
