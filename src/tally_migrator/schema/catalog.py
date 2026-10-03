"""Data catalog generator.

Produces a persistent schema/data_catalog.json detailing:
- Collections (master, transaction, config, child)
- Field definitions & SQL mappings
- Preservation strategies for unmapped fields/UDFs
- Data types, nullable, primary key, change markers
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tally_migrator.schema.discovery import SchemaDiscovery
from tally_migrator.schema.generator import VOUCHER_SUB_TABLES

logger = logging.getLogger(__name__)


class DataCatalogGenerator:
    """Generates the data_catalog.json metadata artifact."""

    def __init__(self, schema_dir: Path):
        self.schema_dir = schema_dir
        self.discovered_path = schema_dir / "discovered_schema.json"
        self.catalog_path = schema_dir / "data_catalog.json"

    def generate(self) -> dict[str, Any]:
        """Generate catalog metadata structure."""
        discovered = None
        if self.discovered_path.exists():
            try:
                discovered = SchemaDiscovery.load(self.discovered_path)
            except Exception as exc:
                logger.warning("Could not load discovered schema from %s: %s", self.discovered_path, exc)

        collections_catalog: dict[str, Any] = {}

        if discovered and discovered.collections:
            for name, coll in sorted(discovered.collections.items()):
                fields_info: dict[str, Any] = {}
                for fname, fdef in coll.fields.items():
                    fields_info[fname] = {
                        "name": fname,
                        "tally_type": fdef.tally_type.value if hasattr(fdef.tally_type, "value") else str(fdef.tally_type),
                        "sql_column": fname.lower(),
                        "sql_type": fdef.sql_type,
                        "nullable": fdef.nullable,
                        "is_primary_key": fdef.is_primary_key,
                        "is_change_marker": fdef.is_change_marker,
                        "preservation_strategy": "explicit_column",
                        "status": "mapped",
                    }

                collections_catalog[name] = {
                    "collection_name": name,
                    "category": coll.category.value if hasattr(coll.category, "value") else str(coll.category),
                    "sql_table_name": coll.sql_table_name or name.lower(),
                    "primary_key_field": coll.primary_key_field,
                    "change_marker_field": coll.change_marker_field,
                    "child_collections": coll.child_collections,
                    "fields": fields_info,
                    "unmapped_field_preservation": "raw_unmapped_json",
                    "udf_preservation": "raw_unmapped_json",
                }

        # Add voucher sub-tables to catalog
        for table_name, defn in VOUCHER_SUB_TABLES.items():
            fields_info = {}
            for col_name, col_type, nullable, is_pk in defn["columns"]:
                fields_info[col_name] = {
                    "name": col_name,
                    "tally_type": "text",
                    "sql_column": col_name,
                    "sql_type": col_type,
                    "nullable": nullable,
                    "is_primary_key": is_pk,
                    "is_change_marker": False,
                    "preservation_strategy": "explicit_column",
                    "status": "mapped",
                }
            collections_catalog[table_name] = {
                "collection_name": table_name,
                "category": "child",
                "sql_table_name": table_name,
                "parent_collection": "Voucher",
                "foreign_key": defn["fk"],
                "description": defn["description"],
                "fields": fields_info,
                "unmapped_field_preservation": "raw_unmapped_json",
                "udf_preservation": "raw_unmapped_json",
            }

        catalog = {
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "schema_file": str(self.discovered_path),
            },
            "collections_count": len(collections_catalog),
            "collections": collections_catalog,
            "preservation_rules": {
                "known_fields": "mapped_to_sql_columns",
                "unmapped_fields": "serialized_in_raw_unmapped_json",
                "udf_fields": "serialized_in_raw_unmapped_json",
                "nested_unknown_structures": "serialized_in_raw_unmapped_json",
            },
        }

        self.save(catalog)
        return catalog

    def save(self, catalog: dict[str, Any]) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
        logger.info("Data catalog saved to %s", self.catalog_path)
