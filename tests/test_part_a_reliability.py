"""Unit tests verifying Part A reliability and completeness requirements A1-A18."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tally_migrator.config import AppConfig
from tally_migrator.exceptions import (
    LockError,
    SchemaValidationError,
    SQLExecutionError,
    TallyQueryError,
)
from tally_migrator.migration.mapper import MigrationMapper
from tally_migrator.migration.pipeline import MigrationPipeline
from tally_migrator.schema.discovery import SchemaDiscovery
from tally_migrator.schema.models import (
    CollectionCategory,
    DiscoveredCollection,
    DiscoveredField,
    DiscoveredSchema,
    FieldType,
)
from tally_migrator.sql.connection import SQLConnection
from tally_migrator.sql.lock import SQLProcessLock
from tally_migrator.sql.upsert import BatchUpsertEngine
from tally_migrator.tally.xml_client import (
    StreamingTallyXMLParser,
    XMLTallyClient,
    _xml_escape,
)
from tests.fixtures.tally_fixtures import SAMPLE_DISCOVERED_SCHEMA


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


def test_a1_merge_failure_propagation():
    mock_sql = MagicMock()
    mock_cursor = MagicMock()
    mock_sql.cursor.return_value = mock_cursor
    mock_sql.transaction.return_value.__enter__ = MagicMock()
    mock_sql.transaction.return_value.__exit__.return_value = False
    mock_cursor.execute.side_effect = Exception("MERGE constraint violation")

    engine = BatchUpsertEngine(mock_sql, "dbo", "ledger", "guid", run_id="run-a1")
    records = [{"guid": "g1", "name": "Ledger 1"}]

    res = engine.upsert_batch(records)
    assert res.failed == 1


def test_a2_failed_transformation_accounting(mock_config):
    pipeline = MigrationPipeline(mock_config, run_id="run-a2", dry_run=True)
    pipeline._tally_client = MagicMock()
    pipeline._tally_client.fetch_all.return_value = [{"GUID": "g1"}]

    mapping = MagicMock()
    mapping.source_collection = "Ledger"

    with patch.object(pipeline, "_transform_record", return_value=None):
        stats = pipeline._sync_collection("Ledger", incremental=False)
        assert stats.rows_failed == 1


def test_a3_missing_collection_mappings(mock_config):
    pipeline = MigrationPipeline(mock_config, run_id="run-a3", dry_run=True)
    pipeline._tally_client = MagicMock()
    pipeline._mapper = MagicMock()
    pipeline._mapper.get.return_value = None

    stats = pipeline._sync_collection("UnknownCollection", incremental=False)
    assert stats.rows_failed == 1
    assert len(stats.errors) > 0


def test_a4_allocation_ownership():
    raw_voucher = {
        "GUID": "vch-a4",
        "VOUCHERNUMBER": "VCH-A4",
        "ALLLEDGERENTRIES.LIST": [
            {
                "LEDGERNAME": "Debtor A",
                "BILLALLOCATIONS.LIST": [{"NAME": "INV-1", "AMOUNT": "500"}],
                "BANKALLOCATIONS.LIST": [{"INSTRUMENTNUMBER": "CHQ1", "AMOUNT": "500"}],
            }
        ],
        "ALLINVENTORYENTRIES.LIST": [
            {
                "STOCKITEMNAME": "Item A",
                "BATCHALLOCATIONS.LIST": [{"BATCHNAME": "BAT-1", "AMOUNT": "500"}],
            }
        ],
    }
    transformed_voucher = {"guid": "vch-a4"}

    mock_sql = MagicMock()
    pipeline = MigrationPipeline(MagicMock(), run_id="run-a4", dry_run=False)
    pipeline._sql = mock_sql

    with patch("tally_migrator.sql.upsert.BatchUpsertEngine") as mock_engine:
        pipeline._sync_voucher_children([(raw_voucher, transformed_voucher)])
        created_tables = {c[0][2] for c in mock_engine.call_args_list}
        assert "voucher_bill_allocation" in created_tables
        assert "voucher_bank_entry" in created_tables
        assert "voucher_batch_allocation" in created_tables


def test_a5_empty_child_sets_delete_stale_children():
    mock_sql = MagicMock()
    mock_cursor = MagicMock()
    mock_sql.cursor.return_value = mock_cursor

    pipeline = MigrationPipeline(MagicMock(), run_id="run-a5", dry_run=False)
    pipeline._sql = mock_sql

    raw_voucher_empty = {"GUID": "vch-empty"}
    transformed_voucher_empty = {"guid": "vch-empty"}

    pipeline._sync_voucher_children([(raw_voucher_empty, transformed_voucher_empty)])

    delete_calls = [c for c in mock_cursor.execute.call_args_list if "DELETE FROM" in c[0][0]]
    assert len(delete_calls) == 7


def test_a6_child_replacement_transactional():
    mock_sql = MagicMock()
    mock_cursor = MagicMock()
    mock_sql.cursor.return_value = mock_cursor
    mock_sql.transaction.side_effect = Exception("DB transaction error")

    pipeline = MigrationPipeline(MagicMock(), run_id="run-a6", dry_run=False)
    pipeline._sql = mock_sql

    raw_vch = {"GUID": "v1"}
    trans_vch = {"guid": "v1"}

    with pytest.raises(Exception, match="DB transaction error"):
        pipeline._sync_voucher_children([(raw_vch, trans_vch)])


def test_a7_parameterized_voucher_delete_sql():
    pipeline = MigrationPipeline(MagicMock(), run_id="run-a7", dry_run=False)
    mock_cursor = MagicMock()

    guids = [f"guid-special'-{i}" for i in range(1200)]
    pipeline._delete_voucher_children(mock_cursor, "dbo", "voucher_ledger_entry", guids)

    assert mock_cursor.execute.call_count == 2
    first_call_sql, first_call_args = mock_cursor.execute.call_args_list[0][0]
    assert "WHERE [voucher_guid] IN (" in first_call_sql
    assert len(first_call_args) == 900
    assert first_call_args[0] == "guid-special'-0"


def test_a8_xml_value_escaping():
    assert _xml_escape("AT&T & Co <'Store'>") == "AT&amp;T &amp; Co &lt;&apos;Store&apos;&gt;"

    client = XMLTallyClient(MagicMock())
    client.config.company_name = "Acme & Co <Private> 'Ltd'"
    req = client._build_collection_request("Ledger")

    assert "Acme &amp; Co &lt;Private&gt; &apos;Ltd&apos;" in req


def test_a9_incremental_filter_formula():
    client = XMLTallyClient(MagicMock())
    req = client._build_collection_request("Voucher", filter_expr="$$Number:$ALTERID > 100")

    assert "<FILTER>IncFilter</FILTER>" in req
    assert '<SYSTEM TYPE="Formulae" NAME="IncFilter">$$Number:$ALTERID &gt; 100</SYSTEM>' in req


def test_a10_explicit_voucher_detail_fetch():
    client = XMLTallyClient(MagicMock())
    req = client._build_collection_request("Voucher")

    assert "<FETCH>ALLLEDGERENTRIES.*</FETCH>" in req
    assert "<FETCH>ALLINVENTORYENTRIES.*</FETCH>" in req
    assert "<FETCH>BILLALLOCATIONS.*</FETCH>" in req
    assert "<FETCH>BANKALLOCATIONS.*</FETCH>" in req


def test_a11_tally_lineerror_handling():
    client = XMLTallyClient(MagicMock())
    error_xml = "<ENVELOPE><VOUCHER><LINEERROR>Invalid TDL collection query</LINEERROR></VOUCHER></ENVELOPE>"

    with pytest.raises(TallyQueryError, match="Invalid TDL collection query"):
        list(client._parse_collection_streaming(error_xml, "VOUCHER"))


def test_a12_streaming_parser_record_detachment():
    parser = StreamingTallyXMLParser("VOUCHER")
    parser.feed(b"<ENVELOPE><VOUCHER><GUID>v1</GUID>")
    assert len(parser._stack) == 2
    root = parser._stack[0]
    child = parser._stack[1]
    assert child in list(root)

    recs = parser.feed(b"</VOUCHER></ENVELOPE>")
    assert len(recs) == 1
    assert child not in list(root)


def test_a13_discovery_client_lifecycle(mock_config):
    mock_client = MagicMock()
    mock_client.list_collections.side_effect = Exception("Network down")
    discovery = SchemaDiscovery(mock_client, mock_config)

    with pytest.raises(Exception, match="Network down"):
        discovery.discover()

    assert mock_client.connect.called
    assert mock_client.close.called


def test_a14_nested_data_json_serialization():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_DISCOVERED_SCHEMA, f)
        schema_path = Path(f.name)

    mapper = MigrationMapper.from_schema_file(schema_path)
    ledger_mapping = mapper.get("Ledger")

    raw_rec = {
        "GUID": "g14",
        "NAME": "Ledger 14",
        "NESTED_LIST": [{"A": 1}, {"B": 2}],
    }

    sql_rec = ledger_mapping.to_sql_record(raw_rec)
    assert sql_rec["raw_unmapped_json"] is not None
    parsed = json.loads(sql_rec["raw_unmapped_json"])
    assert parsed["NESTED_LIST"] == [{"A": 1}, {"B": 2}]


def test_a15_missing_mapped_fields_emit_null():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_DISCOVERED_SCHEMA, f)
        schema_path = Path(f.name)

    mapper = MigrationMapper.from_schema_file(schema_path)
    ledger_mapping = mapper.get("Ledger")

    # Record missing 'NAME' field
    raw_rec = {"GUID": "g15"}
    sql_rec = ledger_mapping.to_sql_record(raw_rec)

    assert "name" in sql_rec
    assert sql_rec["name"] is None


def test_a16_schema_application_errors():
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.execute.side_effect = Exception("Syntax error in DDL")
    sql_conn = SQLConnection.__new__(SQLConnection)
    sql_conn._conn = mock_conn

    with pytest.raises(SQLExecutionError):
        sql_conn.execute_script("INVALID DDL STATEMENT")

    assert mock_conn.rollback.called


def test_a17_fail_closed_locks():
    mock_sql = MagicMock()
    mock_config = MagicMock()
    mock_config.sql.schema_name = "dbo"
    mock_sql.cursor.return_value.execute.side_effect = Exception("Database disk full")

    lock = SQLProcessLock(mock_sql, mock_config)

    with pytest.raises(LockError):
        lock.acquire(run_id="run-a17")

    # Lock release should be a no-op if acquire failed
    lock.release()
    assert lock._lock_holder is None


def test_a18_empty_schema_protection(tmp_path, mock_config):
    mock_config.migration.schema_dir = tmp_path / "empty_schema"
    mock_config.migration.schema_dir.mkdir()
    empty_schema_file = mock_config.migration.schema_dir / "discovered_schema.json"
    empty_schema_file.write_text(json.dumps({"version": "1.0", "collections": {}}))

    pipeline = MigrationPipeline(mock_config, run_id="run-a18", dry_run=True)
    pipeline._tally_client = MagicMock()

    with pytest.raises(SchemaValidationError):
        pipeline._setup()

    res = pipeline.run_full_sync()
    assert res.success is False
