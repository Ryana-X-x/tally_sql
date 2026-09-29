"""Incremental synchronisation runner.

Incremental sync:
1. Reads last sync state per collection from SQL Server
2. Fetches only records changed since last sync
3. Applies upserts
4. Updates sync state markers

Change detection uses Tally's ALTERID field (monotonically increasing integer
that Tally increments on every modification). This is the most reliable
change indicator available via standard Tally interfaces.

Limitations:
- ALTERID is per-company and resets if the Tally company data is rebuilt
- Deleted records: Tally does not expose deleted records via standard APIs
  The system flags this limitation and does not silently skip deletion handling
- Optional/cancelled vouchers are tracked via ISCANCELLED field

If a collection does not expose ALTERID or an equivalent field, the system
falls back to full-fetch for that collection and logs the limitation.
"""

from __future__ import annotations

import logging

from tally_migrator.logging.logger import MigrationResult
from tally_migrator.migration.pipeline import MigrationPipeline

logger = logging.getLogger(__name__)

# Collections known to NOT have reliable change markers
NO_CHANGE_MARKER_COLLECTIONS: set[str] = set()


class IncrementalSyncRunner:
    """Runs an incremental Tally -> SQL synchronisation.

    Uses ALTERID-based change detection where available.
    Falls back to full collection fetch when change markers are unavailable.
    """

    def __init__(self, config, run_id: str):
        self.config = config
        self.run_id = run_id

    def run(self) -> MigrationResult:
        logger.info("IncrementalSyncRunner starting run_id=%s", self.run_id)

        # Log deletion limitation
        logger.warning(
            "DELETION LIMITATION: Tally standard APIs do not expose deleted "
            "record identifiers. Deleted Tally records will NOT be automatically "
            "removed from SQL Server. Use periodic reconciliation if needed."
        )

        pipeline = MigrationPipeline(
            config=self.config,
            run_id=self.run_id,
            dry_run=self.config.migration.dry_run,
        )
        result = pipeline.run_incremental_sync()
        return result


class DeletionReconciler:
    """Periodic deletion reconciliation.

    Because Tally standard APIs do not expose deleted records,
    this reconciler performs a periodic full comparison:

    1. Fetch all GUIDs from Tally
    2. Fetch all GUIDs from SQL Server
    3. Identify GUIDs in SQL but NOT in Tally
    4. These are candidates for soft-delete (flagged, not hard-deleted)

    This is NOT run during normal incremental sync.
    It should be run periodically (e.g. weekly) as a maintenance operation.
    """

    def __init__(self, config, sql_connection, tally_client):
        self.config = config
        self.sql = sql_connection
        self.tally = tally_client

    def reconcile(
        self,
        collection_name: str,
        table_name: str,
        key_column: str = "guid",
        soft_delete: bool = True,
    ) -> dict:
        """Compare Tally GUIDs vs SQL GUIDs for a collection.

        Args:
            soft_delete: If True, mark candidates with is_deleted=True.
                         If False, only report (do not modify SQL).

        Returns:
            dict with 'tally_count', 'sql_count', 'candidates_for_deletion'.
        """
        logger.info("Reconciling deletions for '%s'", collection_name)

        # Fetch all GUIDs from Tally
        tally_guids: set[str] = set()
        for record in self.tally.fetch_all(collection_name, fields=["GUID"]):
            guid = record.get("GUID") or record.get("guid")
            if guid:
                tally_guids.add(str(guid).strip())

        # Fetch all GUIDs from SQL
        schema = self.config.sql.schema_name
        cursor = self.sql.cursor()
        cursor.execute(
            f"SELECT [{key_column}] FROM [{schema}].[{table_name}] "
            f"WHERE [{key_column}] IS NOT NULL"
        )
        sql_guids = {row[0] for row in cursor.fetchall()}
        cursor.close()

        # Find records in SQL but not in Tally
        candidates = sql_guids - tally_guids

        logger.info(
            "Reconciliation '%s': tally=%d sql=%d candidates=%d",
            collection_name, len(tally_guids), len(sql_guids), len(candidates)
        )

        if candidates and soft_delete:
            # Soft-delete by adding is_deleted flag (if column exists)
            # This does NOT hard-delete
            for guid in candidates:
                try:
                    cursor = self.sql.cursor()
                    cursor.execute(
                        f"UPDATE [{schema}].[{table_name}] "
                        f"SET [_is_deleted]=1 WHERE [{key_column}]=?",
                        (guid,)
                    )
                    self.sql.commit()
                    cursor.close()
                except Exception as exc:
                    logger.warning("Soft-delete failed for guid=%s: %s", guid, exc)

        return {
            "collection": collection_name,
            "tally_count": len(tally_guids),
            "sql_count": len(sql_guids),
            "candidates_for_deletion": sorted(candidates),
        }
