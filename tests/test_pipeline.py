"""Unit tests for Migration Pipeline and CLI commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tally_migrator.cli import _build_parser, main
from tally_migrator.config import AppConfig
from tally_migrator.migration.pipeline import MigrationPipeline
from tally_migrator.schema.discovery import SchemaDiscovery
from tally_migrator.schema.models import (
    CollectionCategory,
    DiscoveredCollection,
    DiscoveredField,
    DiscoveredSchema,
    FieldType,
)


@pytest.fixture
def mock_config(tmp_path):
    cfg = AppConfig()
    cfg.tally.host = "localhost"
    cfg.tally.port = 9000
    cfg.tally.protocol = "xml"
    cfg.sql.server = "localhost"
    cfg.sql.database = "TestDB"
    cfg.sql.trusted_connection = True
    cfg.migration.schema_dir = tmp_path / "schema"
    cfg.migration.sql_dir = tmp_path / "sql"
    cfg.migration.dry_run = True

    # Create a dummy discovered_schema.json
    cfg.migration.schema_dir.mkdir(parents=True, exist_ok=True)
    schema = DiscoveredSchema(
        collections={
            "Ledger": DiscoveredCollection(
                name="Ledger",
                category=CollectionCategory.MASTER,
                fields={
                    "GUID": DiscoveredField(name="GUID", tally_type=FieldType.GUID, is_primary_key=True),
                    "NAME": DiscoveredField(name="NAME", tally_type=FieldType.TEXT),
                    "ALTERID": DiscoveredField(name="ALTERID", tally_type=FieldType.INTEGER, is_change_marker=True),
                },
                primary_key_field="GUID",
                change_marker_field="ALTERID",
            )
        }
    )
    SchemaDiscovery.save(schema, cfg.migration.schema_dir / "discovered_schema.json")
    return cfg


class TestMigrationPipeline:
    @patch("tally_migrator.tally.factory.create_tally_client")
    def test_run_full_sync_dry_run(self, mock_create_client, mock_config):
        mock_client = MagicMock()
        mock_client.list_collections.return_value = ["Ledger"]
        mock_client.fetch_all.return_value = iter([
            {"GUID": "g-101", "NAME": "Capital", "ALTERID": "1"}
        ])
        mock_create_client.return_value = mock_client

        pipeline = MigrationPipeline(mock_config, run_id="test-run", dry_run=True)
        res = pipeline.run_full_sync()
        assert res.run_id == "test-run"
        assert res.dry_run is True
        assert res.success is True
        assert len(res.collections) == 1
        assert res.collections[0].name == "Ledger"
        assert res.collections[0].rows_read == 1

    @patch("tally_migrator.tally.factory.create_tally_client")
    def test_run_incremental_sync_dry_run(self, mock_create_client, mock_config):
        mock_client = MagicMock()
        mock_client.list_collections.return_value = ["Ledger"]
        mock_client.fetch_since.return_value = iter([
            {"GUID": "g-102", "NAME": "Sales", "ALTERID": "5"}
        ])
        mock_client.fetch_all.return_value = iter([
            {"GUID": "g-102", "NAME": "Sales", "ALTERID": "5"}
        ])
        mock_create_client.return_value = mock_client

        pipeline = MigrationPipeline(mock_config, run_id="test-inc-run", dry_run=True)
        res = pipeline.run_incremental_sync()
        assert res.run_id == "test-inc-run"
        assert res.success is True
        assert res.collections[0].rows_read == 1

    def test_numeric_alterid_comparison(self):
        from tally_migrator.migration.pipeline import _is_higher_marker
        assert _is_higher_marker("1000", "99") is True
        assert _is_higher_marker("99", "1000") is False
        assert _is_higher_marker("500", "500") is False
        assert _is_higher_marker("10", None) is True

    @patch("tally_migrator.tally.factory.create_tally_client")
    @patch("tally_migrator.sql.connection.SQLConnection")
    @patch("tally_migrator.sql.lock.SQLProcessLock")
    @patch("tally_migrator.sql.sync_state.SyncStateManager")
    def test_sync_state_does_not_advance_on_failure(
        self, mock_state_mgr_cls, mock_lock_cls, mock_sql_cls, mock_create_client, mock_config
    ):
        mock_config.migration.dry_run = False
        mock_client = MagicMock()
        mock_client.list_collections.return_value = ["Ledger"]
        mock_client.fetch_all.return_value = iter([
            {"GUID": "g-1", "NAME": "Leg1", "ALTERID": "100"},
            {"GUID": "g-2", "NAME": "Leg2", "ALTERID": "200"},
        ])
        mock_create_client.return_value = mock_client

        mock_state_mgr = MagicMock()
        mock_state_mgr_cls.return_value = mock_state_mgr

        pipeline = MigrationPipeline(mock_config, run_id="run-fail-test", dry_run=False)
        # Mock _write_batch to report a failed row
        with patch.object(pipeline, "_write_batch") as mock_write:
            from tally_migrator.sql.upsert import UpsertResult
            mock_write.return_value = UpsertResult(inserted=1, failed=1)
            res = pipeline.run_full_sync()

            assert res.success is False
            # Ensure mark_failed was called instead of mark_success
            mock_state_mgr.mark_failed.assert_called_once()
            mock_state_mgr.mark_success.assert_not_called()

    @patch("tally_migrator.tally.factory.create_tally_client")
    @patch("tally_migrator.sql.connection.SQLConnection")
    @patch("tally_migrator.sql.lock.SQLProcessLock")
    def test_pipeline_acquires_and_releases_lock(
        self, mock_lock_cls, mock_sql_cls, mock_create_client, mock_config
    ):
        mock_config.migration.dry_run = False
        mock_client = MagicMock()
        mock_client.list_collections.return_value = []
        mock_create_client.return_value = mock_client

        mock_lock = MagicMock()
        mock_lock_cls.return_value = mock_lock

        pipeline = MigrationPipeline(mock_config, run_id="run-lock-test", dry_run=False)
        pipeline.run_full_sync()

        mock_lock.acquire.assert_called_once_with(run_id="run-lock-test")
        mock_lock.release.assert_called_once()


class TestCLI:
    def test_cli_parser_builds(self):
        parser = _build_parser()
        args = parser.parse_args(["test-connection"])
        assert args.command == "test-connection"

    def test_cli_version_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "tally_migrator" in captured.out

    @patch("tally_migrator.cli._cmd_test_connection")
    def test_cli_main_dispatches_command(self, mock_cmd):
        mock_cmd.return_value = 0
        with pytest.raises(SystemExit) as exc_info:
            main(["test-connection"])
        assert exc_info.value.code == 0
        mock_cmd.assert_called_once()
