"""Tests for data catalog generation, coverage reporting, and reconciliation."""

from __future__ import annotations

import json
from pathlib import Path

from tally_migrator.config import AppConfig
from tally_migrator.reports.coverage import CoverageReporter
from tally_migrator.reports.reconcile import Reconciler
from tally_migrator.schema.catalog import DataCatalogGenerator


def test_data_catalog_generator(tmp_path: Path):
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    discovered_file = schema_dir / "discovered_schema.json"
    discovered_file.write_text(
        json.dumps({
            "version": "1.0",
            "collections": {
                "Ledger": {
                    "name": "Ledger",
                    "category": "master",
                    "fields": {
                        "GUID": {"name": "GUID", "tally_type": "guid", "sql_type": "NVARCHAR(64)", "nullable": False, "is_primary_key": True, "is_change_marker": False},
                        "NAME": {"name": "NAME", "tally_type": "text", "sql_type": "NVARCHAR(255)", "nullable": True, "is_primary_key": False, "is_change_marker": False},
                    },
                    "child_collections": [],
                    "sql_table_name": "ledger",
                    "primary_key_field": "GUID",
                    "change_marker_field": "ALTERID",
                }
            }
        })
    )

    generator = DataCatalogGenerator(schema_dir)
    catalog = generator.generate()

    assert catalog["version"] == "1.0"
    assert catalog["collections_count"] > 0
    assert "Ledger" in catalog["collections"]
    assert catalog["collections"]["Ledger"]["unmapped_field_preservation"] == "raw_unmapped_json"
    assert "voucher_ledger_entry" in catalog["collections"]
    assert generator.catalog_path.exists()


def test_coverage_reporter():
    cfg = AppConfig()
    reporter = CoverageReporter(cfg)
    report = reporter.generate_report()

    assert "TALLY DATA COVERAGE REPORT" in report
    assert "DISCOVERED COLLECTIONS" in report
    assert "MASTER RECORDS" in report
    assert "CHILD RECORDS" in report
    assert "raw_unmapped_json" in report


def test_reconciler():
    cfg = AppConfig()
    reconciler = Reconciler(cfg)
    report = reconciler.reconcile()

    assert "TALLY VS SQL RECONCILIATION REPORT" in report
    assert "RECONCILIATION WITH OLD TallyDB (~811,541 ROWS)" in report
    assert "TRN_VOUCHER / VOUCHER_DETAIL" in report
    assert "Combined New TallySQL Total Rows" in report
