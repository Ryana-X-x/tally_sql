"""Tests for schema discovery."""

from __future__ import annotations

import json

from tests.fixtures.tally_fixtures import SAMPLE_DISCOVERED_SCHEMA


class TestSchemaDiscoveryLoad:
    def test_load_save_roundtrip(self):
        from tally_migrator.schema.models import DiscoveredSchema

        schema = DiscoveredSchema.from_dict(SAMPLE_DISCOVERED_SCHEMA)
        assert "Ledger" in schema.collections

    def test_collection_has_fields(self):
        from tally_migrator.schema.models import DiscoveredSchema
        schema = DiscoveredSchema.from_dict(SAMPLE_DISCOVERED_SCHEMA)
        ledger = schema.collections["Ledger"]
        assert "GUID" in ledger.fields
        assert "NAME" in ledger.fields
        assert "ALTERID" in ledger.fields

    def test_primary_key_field(self):
        from tally_migrator.schema.models import DiscoveredSchema
        schema = DiscoveredSchema.from_dict(SAMPLE_DISCOVERED_SCHEMA)
        ledger = schema.collections["Ledger"]
        assert ledger.primary_key_field == "GUID"

    def test_change_marker_field(self):
        from tally_migrator.schema.models import DiscoveredSchema
        schema = DiscoveredSchema.from_dict(SAMPLE_DISCOVERED_SCHEMA)
        ledger = schema.collections["Ledger"]
        assert ledger.change_marker_field == "ALTERID"

    def test_save_and_reload(self, tmp_path):
        from tally_migrator.schema.discovery import SchemaDiscovery
        from tally_migrator.schema.models import DiscoveredSchema
        schema = DiscoveredSchema.from_dict(SAMPLE_DISCOVERED_SCHEMA)
        path = tmp_path / "test_schema.json"

        # Save
        with open(path, "w") as f:
            json.dump(schema.to_dict(), f)

        # Reload
        reloaded = SchemaDiscovery.load(path)
        assert "Ledger" in reloaded.collections
