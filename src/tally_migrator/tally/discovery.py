"""Tally-side discovery helpers.

These helpers interrogate a live Tally connection to determine
the actual available collections and fields.

This is separate from schema/discovery.py which operates on the
cached discovered_schema.json.
"""

from __future__ import annotations

import logging
from typing import Any

from tally_migrator.tally.base_client import BaseTallyClient
from tally_migrator.tally.xml_client import KNOWN_COLLECTIONS

logger = logging.getLogger(__name__)


class TallyDiscoverer:
    """Interrogates a live Tally connection to discover schema information."""

    def __init__(self, client: BaseTallyClient):
        self.client = client

    def discover_collections(self) -> list[str]:
        """Return list of available collection names."""
        try:
            return self.client.list_collections()
        except Exception as exc:
            logger.warning("Could not list collections dynamically: %s", exc)
            return list(KNOWN_COLLECTIONS.keys())

    def discover_fields(
        self, collection_name: str, sample_size: int = 10
    ) -> dict[str, Any]:
        """Discover fields for a collection by examining sample records."""
        info = self.client.describe_collection(collection_name)
        fields = info.get("fields", [])

        # Try to enhance with actual sample data
        samples: list[dict] = []
        try:
            for i, record in enumerate(self.client.fetch_all(collection_name)):
                samples.append(record)
                if i >= sample_size - 1:
                    break
        except Exception as exc:
            logger.warning("Could not fetch samples for %s: %s", collection_name, exc)

        # Merge field info from samples
        sample_keys: set[str] = set()
        for s in samples:
            sample_keys.update(s.keys())

        declared_names = {f["name"] if isinstance(f, dict) else f for f in fields}
        extra_fields = sample_keys - declared_names

        if extra_fields:
            logger.info(
                "Collection %s: discovered %d extra fields via sampling: %s",
                collection_name, len(extra_fields), sorted(extra_fields)
            )

        return {
            "name": collection_name,
            "fields": fields,
            "extra_fields_from_samples": sorted(extra_fields),
            "sample_count": len(samples),
            "category": info.get("category", "unknown"),
            "key_field": info.get("key_field"),
            "alteration_field": info.get("alteration_field"),
            "child_collections": info.get("child_collections", []),
        }

    def classify_collection(self, collection_name: str) -> str:
        """Return 'master', 'transaction', or 'config'."""
        known = KNOWN_COLLECTIONS.get(collection_name, {})
        return known.get("category", "unknown")

    def estimate_row_count(self, collection_name: str) -> int:
        """Estimate record count for planning purposes."""
        try:
            return self.client.count_records(collection_name)
        except Exception:
            return 0
