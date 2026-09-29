"""Schema representation models.

These models represent the Tally schema as discovered,
and serve as input to the SQL schema generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FieldType(str, Enum):
    TEXT = "text"
    DATE = "date"
    DATETIME = "datetime"
    AMOUNT = "amount"
    QUANTITY = "quantity"
    RATE = "rate"
    LOGICAL = "logical"
    INTEGER = "integer"
    GUID = "guid"
    LARGE_TEXT = "large_text"
    UNKNOWN = "unknown"


class CollectionCategory(str, Enum):
    MASTER = "master"
    TRANSACTION = "transaction"
    CONFIG = "config"
    UNKNOWN = "unknown"


@dataclass
class DiscoveredField:
    name: str
    tally_type: FieldType = FieldType.TEXT
    sql_type: Optional[str] = None          # e.g. 'NVARCHAR(255)'
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references_collection: Optional[str] = None
    references_field: Optional[str] = None
    is_change_marker: bool = False          # used for incremental sync
    sample_values: list[Any] = field(default_factory=list)
    description: str = ""
    is_custom_tdl: bool = False             # true if not in standard Tally schema
    mapped: bool = True                     # false if discovered but not yet mapped

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "tally_type": self.tally_type.value,
            "sql_type": self.sql_type,
            "nullable": self.nullable,
            "is_primary_key": self.is_primary_key,
            "is_foreign_key": self.is_foreign_key,
            "references_collection": self.references_collection,
            "references_field": self.references_field,
            "is_change_marker": self.is_change_marker,
            "sample_values": [str(v) for v in self.sample_values[:5]],
            "description": self.description,
            "is_custom_tdl": self.is_custom_tdl,
            "mapped": self.mapped,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DiscoveredField":
        return cls(
            name=d["name"],
            tally_type=FieldType(d.get("tally_type", "unknown")),
            sql_type=d.get("sql_type"),
            nullable=d.get("nullable", True),
            is_primary_key=d.get("is_primary_key", False),
            is_foreign_key=d.get("is_foreign_key", False),
            references_collection=d.get("references_collection"),
            references_field=d.get("references_field"),
            is_change_marker=d.get("is_change_marker", False),
            sample_values=d.get("sample_values", []),
            description=d.get("description", ""),
            is_custom_tdl=d.get("is_custom_tdl", False),
            mapped=d.get("mapped", True),
        )


@dataclass
class DiscoveredCollection:
    name: str
    category: CollectionCategory = CollectionCategory.UNKNOWN
    fields: dict[str, DiscoveredField] = field(default_factory=dict)
    child_collections: list[str] = field(default_factory=list)
    parent_collection: Optional[str] = None
    sql_table_name: Optional[str] = None
    primary_key_field: Optional[str] = None
    change_marker_field: Optional[str] = None
    estimated_row_count: int = 0
    unmapped_fields: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category.value,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "child_collections": self.child_collections,
            "parent_collection": self.parent_collection,
            "sql_table_name": self.sql_table_name,
            "primary_key_field": self.primary_key_field,
            "change_marker_field": self.change_marker_field,
            "estimated_row_count": self.estimated_row_count,
            "unmapped_fields": self.unmapped_fields,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DiscoveredCollection":
        fields = {
            k: DiscoveredField.from_dict(v)
            for k, v in d.get("fields", {}).items()
        }
        return cls(
            name=d["name"],
            category=CollectionCategory(d.get("category", "unknown")),
            fields=fields,
            child_collections=d.get("child_collections", []),
            parent_collection=d.get("parent_collection"),
            sql_table_name=d.get("sql_table_name"),
            primary_key_field=d.get("primary_key_field"),
            change_marker_field=d.get("change_marker_field"),
            estimated_row_count=d.get("estimated_row_count", 0),
            unmapped_fields=d.get("unmapped_fields", []),
            description=d.get("description", ""),
        )


@dataclass
class DiscoveredSchema:
    """Complete schema discovered from a Tally installation."""
    version: str = "1.0"
    discovery_timestamp: str = ""
    tally_host: str = ""
    tally_company: str = ""
    collections: dict[str, DiscoveredCollection] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "discovery_timestamp": self.discovery_timestamp,
            "tally_host": self.tally_host,
            "tally_company": self.tally_company,
            "collections": {
                k: v.to_dict() for k, v in self.collections.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DiscoveredSchema":
        collections = {
            k: DiscoveredCollection.from_dict(v)
            for k, v in d.get("collections", {}).items()
        }
        return cls(
            version=d.get("version", "1.0"),
            discovery_timestamp=d.get("discovery_timestamp", ""),
            tally_host=d.get("tally_host", ""),
            tally_company=d.get("tally_company", ""),
            collections=collections,
        )
