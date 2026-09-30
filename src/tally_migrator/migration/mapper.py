"""Field and collection mapping layer.

Maps Tally collection/field names to SQL table/column names.
Acting as the boundary between Tally source schema and SQL target schema.

The mapper is built from the discovered schema.
Custom TDL fields that are not mapped are reported (not silently discarded).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from tally_migrator.schema.models import DiscoveredCollection, DiscoveredSchema

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    source_field: str           # Tally field name
    target_column: str          # SQL column name
    tally_type: str             # e.g. 'text', 'date', 'amount'
    sql_type: str               # e.g. 'NVARCHAR(255)'
    nullable: bool = True
    is_key: bool = False
    transform_hint: str = ""    # optional additional transform instruction


@dataclass
class CollectionMapping:
    source_collection: str      # Tally collection name
    target_table: str           # SQL table name
    schema_name: str            # SQL schema name (e.g. 'dbo')
    key_field: Optional[str]    # SQL column used as upsert key
    change_marker: Optional[str] # field used for incremental sync
    fields: dict[str, FieldMapping] = field(default_factory=dict)
    unmapped_fields: list[str] = field(default_factory=list)  # reported, not silently discarded
    child_mappings: list[str] = field(default_factory=list)

    @property
    def full_table_name(self) -> str:
        return f"[{self.schema_name}].[{self.target_table}]"

    def get_field_type_map(self) -> dict[str, str]:
        """Return {source_field_name: tally_type} for transforms."""
        return {m.source_field: m.tally_type for m in self.fields.values()}

    def to_sql_record(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Map raw Tally record to SQL column dict."""
        out = {}
        for src_field, mapping in self.fields.items():
            if src_field in raw:
                out[mapping.target_column] = raw[src_field]
            elif src_field.upper() in raw:
                out[mapping.target_column] = raw[src_field.upper()]
            elif src_field.lower() in raw:
                out[mapping.target_column] = raw[src_field.lower()]
        # Report unmapped fields found in this record
        known_sources = {m.source_field.upper() for m in self.fields.values()}
        for key in raw:
            if key.upper() not in known_sources and key not in self.unmapped_fields:
                self.unmapped_fields.append(key)
                logger.warning(
                    "Unmapped field '%s' in collection '%s' (record: %s)",
                    key, self.source_collection, raw.get(self.key_field or "GUID", "?")
                )
        return out


class MigrationMapper:
    """Builds CollectionMapping objects from a DiscoveredSchema."""

    def __init__(self, schema: DiscoveredSchema, sql_schema_name: str = "dbo"):
        self.schema = schema
        self.sql_schema_name = sql_schema_name
        self._mappings: dict[str, CollectionMapping] = {}
        self._build()

    @classmethod
    def from_schema_file(cls, schema_path: Path, sql_schema_name: str = "dbo") -> "MigrationMapper":
        from tally_migrator.schema.discovery import SchemaDiscovery
        schema = SchemaDiscovery.load(schema_path)
        return cls(schema, sql_schema_name)

    def _build(self) -> None:
        for coll_name, coll in self.schema.collections.items():
            mapping = self._build_collection_mapping(coll)
            self._mappings[coll_name] = mapping

    def _build_collection_mapping(self, coll: DiscoveredCollection) -> CollectionMapping:
        fields: dict[str, FieldMapping] = {}
        unmapped: list[str] = []

        for field_name, field_def in coll.fields.items():
            if not field_def.mapped:
                unmapped.append(field_name)
                logger.info(
                    "Field '%s.%s' is marked as unmapped (custom TDL?)",
                    coll.name, field_name,
                )
                continue

            target_col = field_def.name.lower()
            fm = FieldMapping(
                source_field=field_def.name,
                target_column=target_col,
                tally_type=field_def.tally_type.value,
                sql_type=field_def.sql_type or "NVARCHAR(255)",
                nullable=field_def.nullable,
                is_key=field_def.is_primary_key,
            )
            fields[field_name] = fm

        # Report unmapped fields
        if unmapped:
            logger.warning(
                "Collection '%s': %d unmapped field(s): %s",
                coll.name, len(unmapped), unmapped
            )

        return CollectionMapping(
            source_collection=coll.name,
            target_table=coll.sql_table_name or coll.name.lower(),
            schema_name=self.sql_schema_name,
            key_field=coll.primary_key_field.lower() if coll.primary_key_field else None,
            change_marker=coll.change_marker_field.lower() if coll.change_marker_field else None,
            fields=fields,
            unmapped_fields=unmapped,
            child_mappings=coll.child_collections,
        )

    def get(self, collection_name: str) -> Optional[CollectionMapping]:
        return self._mappings.get(collection_name)

    def all_mappings(self) -> list[CollectionMapping]:
        return list(self._mappings.values())

    def report_unmapped(self) -> dict[str, list[str]]:
        """Return {collection: [unmapped_fields]} for all collections with unmapped fields."""
        return {
            coll_name: m.unmapped_fields
            for coll_name, m in self._mappings.items()
            if m.unmapped_fields
        }
