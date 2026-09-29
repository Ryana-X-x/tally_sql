"""Schema discovery subsystem.

Discovers Tally schema from a live connection and persists it to
schema/discovered_schema.json.

The discovered schema is SCHEMA METADATA ONLY - not extracted row data.

Workflow:
    discover -> save(discovered_schema.json) -> generate-schema -> sync
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tally_migrator.schema.models import (
    CollectionCategory,
    DiscoveredCollection,
    DiscoveredField,
    DiscoveredSchema,
    FieldType,
)
from tally_migrator.tally.base_client import BaseTallyClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type inference rules
# ---------------------------------------------------------------------------

FIELD_TYPE_HINTS: dict[str, FieldType] = {
    # GUIDs
    "GUID": FieldType.GUID,
    # Dates
    "DATE": FieldType.DATE,
    "EFFECTIVEDATE": FieldType.DATE,
    # Amounts
    "OPENINGBALANCE": FieldType.AMOUNT,
    "CLOSINGBALANCE": FieldType.AMOUNT,
    "OPENINGVALUE": FieldType.AMOUNT,
    "OPENINGRATE": FieldType.RATE,
    # Quantities
    "OPENINGQUANTITY": FieldType.QUANTITY,
    # Logical/Boolean
    "ISREVENUE": FieldType.LOGICAL,
    "ISSUBLEDGER": FieldType.LOGICAL,
    "ISDEEMEDPOSITIVE": FieldType.LOGICAL,
    "AFFECTSGROSSPROFIT": FieldType.LOGICAL,
    "ISCANCELLED": FieldType.LOGICAL,
    "ISOPTIONAL": FieldType.LOGICAL,
    "GSTAPPLICABLE": FieldType.LOGICAL,
    # Integers
    "ALTERID": FieldType.INTEGER,
}

FIELD_DESCRIPTION: dict[str, str] = {
    "GUID": "Globally unique identifier assigned by Tally",
    "NAME": "Primary display name",
    "PARENT": "Parent group or category",
    "OPENINGBALANCE": "Opening balance amount",
    "CLOSINGBALANCE": "Closing balance amount",
    "ALTERID": "Alteration ID - increments on each modification (used for incremental sync)",
    "DATE": "Voucher date",
    "EFFECTIVEDATE": "Effective date (may differ from voucher date)",
    "ISCANCELLED": "Whether the voucher is cancelled",
    "ISOPTIONAL": "Whether the voucher is optional (post-dated)",
    "VOUCHERTYPENAME": "Name of the voucher type (e.g. Sales, Purchase)",
    "VOUCHERNUMBER": "Voucher number",
    "NARRATION": "Free-text narration/description",
    "PARTYLEDGERNAME": "Name of the party ledger account",
}

# Known SQL type mappings by collection/field
SQL_TYPE_MAP: dict[str, dict[str, str]] = {
    "*": {
        "GUID": "NVARCHAR(64)",
        "NAME": "NVARCHAR(255)",
        "PARENT": "NVARCHAR(255)",
        "ALTERID": "BIGINT",
        "DATE": "DATE",
        "EFFECTIVEDATE": "DATE",
        "NARRATION": "NVARCHAR(MAX)",
        "DESCRIPTION": "NVARCHAR(MAX)",
        "OPENINGBALANCE": "DECIMAL(20,4)",
        "CLOSINGBALANCE": "DECIMAL(20,4)",
        "OPENINGRATE": "DECIMAL(20,6)",
        "OPENINGVALUE": "DECIMAL(20,4)",
        "ISREVENUE": "BIT",
        "ISSUBLEDGER": "BIT",
        "ISDEEMEDPOSITIVE": "BIT",
        "AFFECTSGROSSPROFIT": "BIT",
        "ISCANCELLED": "BIT",
        "ISOPTIONAL": "BIT",
        "VOUCHERNUMBER": "NVARCHAR(100)",
        "VOUCHERTYPENAME": "NVARCHAR(100)",
        "PARTYLEDGERNAME": "NVARCHAR(255)",
        "CURRENCYNAME": "NVARCHAR(50)",
        "REFERENCE": "NVARCHAR(255)",
    }
}


class SchemaDiscovery:
    """Discovers Tally schema from a live connection."""

    def __init__(self, client: BaseTallyClient, config):
        self.client = client
        self.config = config

    def discover(self) -> DiscoveredSchema:
        """Perform full schema discovery and return a DiscoveredSchema."""
        logger.info("Starting schema discovery ...")

        schema = DiscoveredSchema(
            version="1.0",
            discovery_timestamp=datetime.now(timezone.utc).isoformat(),
            tally_host=self.config.tally.host,
            tally_company=self.config.tally.company_name,
        )

        collection_names = self.client.list_collections()
        logger.info("Discovered %d collections", len(collection_names))

        for coll_name in collection_names:
            logger.info("Discovering collection: %s", coll_name)
            try:
                coll = self._discover_collection(coll_name)
                schema.collections[coll_name] = coll
            except Exception as exc:
                logger.warning("Failed to discover %s: %s", coll_name, exc)

        logger.info("Schema discovery complete. %d collections.", len(schema.collections))
        return schema

    def _discover_collection(self, name: str) -> DiscoveredCollection:
        info = self.client.describe_collection(name)
        raw_fields = info.get("fields", [])
        category_str = info.get("category", "unknown")

        try:
            category = CollectionCategory(category_str)
        except ValueError:
            category = CollectionCategory.UNKNOWN

        fields: dict[str, DiscoveredField] = {}
        for f in raw_fields:
            if isinstance(f, dict):
                fname = f.get("name", "")
            else:
                fname = str(f)

            if not fname:
                continue

            field_type = FIELD_TYPE_HINTS.get(fname.upper(), FieldType.TEXT)
            sql_type = (
                SQL_TYPE_MAP.get(name, {}).get(fname.upper())
                or SQL_TYPE_MAP["*"].get(fname.upper())
                or _infer_sql_type(field_type)
            )

            is_pk = fname.upper() == info.get("key_field", "").upper()
            is_change = fname.upper() == (info.get("alteration_field") or "").upper()

            df = DiscoveredField(
                name=fname,
                tally_type=field_type,
                sql_type=sql_type,
                nullable=not is_pk,
                is_primary_key=is_pk,
                is_change_marker=is_change,
                description=FIELD_DESCRIPTION.get(fname.upper(), ""),
                mapped=True,
            )
            fields[fname] = df

        # Determine SQL table name (CamelCase to snake_case with tally_ prefix)
        sql_table_name = _to_sql_table_name(name)

        coll = DiscoveredCollection(
            name=name,
            category=category,
            fields=fields,
            child_collections=info.get("child_collections", []),
            sql_table_name=sql_table_name,
            primary_key_field=info.get("key_field"),
            change_marker_field=info.get("alteration_field"),
            description=f"Tally {name} collection ({category_str})",
        )

        return coll

    @staticmethod
    def save(schema: DiscoveredSchema, path: Path) -> None:
        """Persist schema metadata to JSON (SCHEMA METADATA ONLY - not row data)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(schema.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info("Schema saved to %s", path)

    @staticmethod
    def load(path: Path) -> DiscoveredSchema:
        """Load a previously saved discovered schema."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return DiscoveredSchema.from_dict(data)


def _to_sql_table_name(collection_name: str) -> str:
    """Convert Tally collection name to SQL table name."""
    import re
    # Insert underscore before uppercase letters
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", collection_name)
    return s.lower()


def _infer_sql_type(field_type: FieldType) -> str:
    """Map FieldType to SQL Server type."""
    mapping = {
        FieldType.TEXT: "NVARCHAR(255)",
        FieldType.DATE: "DATE",
        FieldType.DATETIME: "DATETIME2",
        FieldType.AMOUNT: "DECIMAL(20,4)",
        FieldType.QUANTITY: "DECIMAL(20,4)",
        FieldType.RATE: "DECIMAL(20,6)",
        FieldType.LOGICAL: "BIT",
        FieldType.INTEGER: "BIGINT",
        FieldType.GUID: "NVARCHAR(64)",
        FieldType.LARGE_TEXT: "NVARCHAR(MAX)",
        FieldType.UNKNOWN: "NVARCHAR(255)",
    }
    return mapping.get(field_type, "NVARCHAR(255)")
