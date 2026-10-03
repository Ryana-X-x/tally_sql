"""Tests for child sync correctness, UDF preservation, and voucher child extraction."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from tally_migrator.migration.mapper import MigrationMapper
from tally_migrator.migration.pipeline import MigrationPipeline
from tests.fixtures.tally_fixtures import SAMPLE_DISCOVERED_SCHEMA


def test_udf_and_unmapped_field_preservation():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_DISCOVERED_SCHEMA, f)
        schema_path = Path(f.name)

    mapper = MigrationMapper.from_schema_file(schema_path)
    ledger_mapping = mapper.get("Ledger")

    raw_record = {
        "GUID": "test-guid-123",
        "NAME": "Test Ledger",
        "PARENT": "Sundry Debtors",
        "ALTERID": "1001",
        "MASTERID": "99",  # Unmapped field
        "ISDELETED": "No",  # Unmapped field
        "UDF:BASICSALESPERSON.LIST": "John Doe",  # UDF field
        "UDF:_UDF_754995785.LIST": [1, 2, 3],  # UDF field list
    }

    sql_record = ledger_mapping.to_sql_record(raw_record)

    assert sql_record["guid"] == "test-guid-123"
    assert sql_record["name"] == "Test Ledger"
    assert "raw_unmapped_json" in sql_record
    assert sql_record["raw_unmapped_json"] is not None

    unmapped = json.loads(sql_record["raw_unmapped_json"])
    assert unmapped["MASTERID"] == "99"
    assert unmapped["ISDELETED"] == "No"
    assert unmapped["UDF:BASICSALESPERSON.LIST"] == "John Doe"
    assert unmapped["UDF:_UDF_754995785.LIST"] == [1, 2, 3]


@patch("tally_migrator.sql.upsert.BatchUpsertEngine")
def test_voucher_child_extraction_all_tables(mock_engine_cls):
    raw_voucher = {
        "GUID": "vch-guid-999",
        "VOUCHERNUMBER": "VCH-001",
        "DATE": "20260401",
        "ALLLEDGERENTRIES.LIST": [
            {
                "LEDGERNAME": "Sales",
                "AMOUNT": "-1000.00",
                "ISDEEMEDPOSITIVE": "No",
                "CATEGORYALLOCATIONS.LIST": [
                    {
                        "COSTCENTRENAME": "Branch A",
                        "AMOUNT": "-1000.00",
                    }
                ],
            }
        ],
        "ALLINVENTORYENTRIES.LIST": [
            {
                "STOCKITEMNAME": "Widget A",
                "BILLEDQTY": "10 Pcs.",
                "RATE": "100/Pcs.",
                "AMOUNT": "1000.00",
                "BATCHALLOCATIONS.LIST": [
                    {
                        "BATCHNAME": "B001",
                        "DESTINATIONGODOWN": "Main Godown",
                        "ACTUALQTY": "10 Pcs.",
                        "AMOUNT": "1000.00",
                    }
                ],
                "ACCOUNTINGALLOCATIONS.LIST": [
                    {
                        "LEDGERNAME": "Sales Account",
                        "AMOUNT": "1000.00",
                    }
                ],
            }
        ],
        "BILLALLOCATIONS.LIST": [
            {
                "NAME": "INV-100",
                "BILLTYPE": "New Ref",
                "AMOUNT": "1000.00",
            }
        ],
        "BANKALLOCATIONS.LIST": [
            {
                "INSTRUMENTNUMBER": "CHQ12345",
                "BANKNAME": "HDFC Bank",
                "AMOUNT": "1000.00",
            }
        ],
    }
    transformed_voucher = {
        "guid": "vch-guid-999",
        "vouchernumber": "VCH-001",
        "date": "20260401",
    }

    mock_sql = MagicMock()
    mock_cursor = MagicMock()
    mock_sql.cursor.return_value = mock_cursor

    mock_config = MagicMock()
    mock_config.migration.dry_run = False
    mock_config.sql.schema_name = "dbo"

    pipeline = MigrationPipeline(mock_config, run_id="test-run", dry_run=False)
    pipeline._sql = mock_sql

    pipeline._sync_voucher_children([(raw_voucher, transformed_voucher)])

    # Verify BatchUpsertEngine was instantiated for sub-tables
    created_tables = {call[0][2] for call in mock_engine_cls.call_args_list}
    assert "voucher_ledger_entry" in created_tables
    assert "voucher_inventory_entry" in created_tables
    assert "voucher_bill_allocation" in created_tables
    assert "voucher_bank_entry" in created_tables
    assert "voucher_batch_allocation" in created_tables
    assert "voucher_cost_centre_allocation" in created_tables
    assert "voucher_accounting_allocation" in created_tables


@patch("tally_migrator.sql.upsert.BatchUpsertEngine")
def test_child_sync_deletion_unconditional(mock_engine_cls):
    mock_sql = MagicMock()
    mock_cursor = MagicMock()
    mock_sql.cursor.return_value = mock_cursor

    mock_config = MagicMock()
    mock_config.migration.dry_run = False
    mock_config.sql.schema_name = "dbo"

    pipeline = MigrationPipeline(mock_config, run_id="test-run", dry_run=False)
    pipeline._sql = mock_sql

    raw_voucher_no_children = {
        "GUID": "vch-guid-empty",
        "VOUCHERNUMBER": "VCH-002",
        "DATE": "20260401",
    }
    transformed_voucher_no_children = {
        "guid": "vch-guid-empty",
        "vouchernumber": "VCH-002",
        "date": "20260401",
    }

    pipeline._sync_voucher_children([(raw_voucher_no_children, transformed_voucher_no_children)])

    delete_calls = [
        call for call in mock_cursor.execute.call_args_list
        if "DELETE FROM" in call[0][0]
    ]

    assert len(delete_calls) == 7
