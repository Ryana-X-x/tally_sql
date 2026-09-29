"""Tests for the migration mapper."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.fixtures.tally_fixtures import SAMPLE_DISCOVERED_SCHEMA


class TestMigrationMapper:
    def _get_schema_path(self) -> Path:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(SAMPLE_DISCOVERED_SCHEMA, f)
            return Path(f.name)

    def test_mapper_loads_schema(self):
        schema_path = self._get_schema_path()
        from tally_migrator.migration.mapper import MigrationMapper
        mapper = MigrationMapper.from_schema_file(schema_path)
        assert mapper.get("Ledger") is not None

    def test_collection_mapping_has_table(self):
        schema_path = self._get_schema_path()
        from tally_migrator.migration.mapper import MigrationMapper
        mapper = MigrationMapper.from_schema_file(schema_path)
        ledger = mapper.get("Ledger")
        assert ledger.target_table == "ledger"

    def test_field_mapping_key(self):
        schema_path = self._get_schema_path()
        from tally_migrator.migration.mapper import MigrationMapper
        mapper = MigrationMapper.from_schema_file(schema_path)
        ledger = mapper.get("Ledger")
        assert ledger.key_field == "guid"

    def test_to_sql_record(self):
        schema_path = self._get_schema_path()
        from tally_migrator.migration.mapper import MigrationMapper
        mapper = MigrationMapper.from_schema_file(schema_path)
        ledger = mapper.get("Ledger")
        raw = {"GUID": "abc", "NAME": "Test", "ALTERID": "100"}
        result = ledger.to_sql_record(raw)
        assert "guid" in result or "GUID" in result
