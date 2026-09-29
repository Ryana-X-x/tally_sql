"""Core migration pipeline.

Orchestrates the Tally -> Python memory -> SQL Server flow.

Key design:
- Bounded batch processing (no full dataset in memory)
- Direct Tally -> SQL (no CSV/file intermediate)
- Per-collection error isolation
- Retry on transient errors
- Sync state tracking
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tally_migrator.sql.upsert import UpsertResult

from tally_migrator.logging.logger import CollectionStats, MigrationResult

logger = logging.getLogger(__name__)


class MigrationPipeline:
    """Core pipeline: Tally -> Python -> SQL Server.

    Processes one collection at a time.
    Each collection is processed in bounded batches.
    """

    def __init__(self, config, run_id: str, dry_run: bool = False):
        self.config = config
        self.run_id = run_id
        self.dry_run = dry_run
        self._tally_client = None
        self._sql = None
        self._mapper = None
        self._state_manager = None

    def _setup(self) -> None:
        """Initialise clients and mapping."""

        from tally_migrator.sql.connection import SQLConnection
        from tally_migrator.sql.sync_state import SyncStateManager
        from tally_migrator.tally.factory import create_tally_client

        self._tally_client = create_tally_client(self.config.tally)
        self._tally_client.connect()

        if not self.dry_run:
            self._sql = SQLConnection(self.config.sql)
            self._state_manager = SyncStateManager(self._sql, self.config)

        # Load mapper from discovered schema
        schema_path = self.config.migration.schema_dir / "discovered_schema.json"
        if schema_path.exists():
            from tally_migrator.migration.mapper import MigrationMapper
            from tally_migrator.schema.discovery import SchemaDiscovery
            schema = SchemaDiscovery.load(schema_path)
            self._mapper = MigrationMapper(schema, self.config.sql.schema_name)
        else:
            logger.warning(
                "No discovered schema found at %s. Run 'discover' first.",
                schema_path,
            )

    def _teardown(self) -> None:
        if self._tally_client:
            self._tally_client.close()
        if self._sql:
            self._sql.close()

    def run_full_sync(self) -> MigrationResult:
        """Execute full synchronisation pipeline."""
        start = datetime.now(timezone.utc)
        result = MigrationResult(
            run_id=self.run_id,
            success=False,
            start_time=start,
            dry_run=self.dry_run,
        )

        try:
            self._setup()
            collections = self._get_collections_to_process()
            logger.info(
                "Full sync started. Collections: %d, run_id=%s, dry_run=%s",
                len(collections), self.run_id, self.dry_run
            )

            for coll_name in collections:
                stats = self._sync_collection(
                    coll_name,
                    incremental=False,
                    since_marker=None,
                )
                result.collections.append(stats)

            result.success = all(
                c.rows_failed == 0 for c in result.collections
            )

        except Exception as exc:
            logger.exception("Full sync pipeline failed: %s", exc)
            result.success = False
        finally:
            result.end_time = datetime.now(timezone.utc)
            self._teardown()

        return result

    def run_incremental_sync(self) -> MigrationResult:
        """Execute incremental synchronisation pipeline."""
        start = datetime.now(timezone.utc)
        result = MigrationResult(
            run_id=self.run_id,
            success=False,
            start_time=start,
            dry_run=self.dry_run,
        )

        try:
            self._setup()
            collections = self._get_collections_to_process()
            logger.info(
                "Incremental sync started. Collections: %d, run_id=%s",
                len(collections), self.run_id
            )

            for coll_name in collections:
                since_marker = self._get_since_marker(coll_name)
                stats = self._sync_collection(
                    coll_name,
                    incremental=True,
                    since_marker=since_marker,
                )
                result.collections.append(stats)

            result.success = all(
                c.rows_failed == 0 for c in result.collections
            )

        except Exception as exc:
            logger.exception("Incremental sync pipeline failed: %s", exc)
            result.success = False
        finally:
            result.end_time = datetime.now(timezone.utc)
            self._teardown()

        return result

    def _get_collections_to_process(self) -> list[str]:
        """Return list of collection names to process."""
        if self.config.migration.collections:
            return self.config.migration.collections
        if self._mapper:
            return [m.source_collection for m in self._mapper.all_mappings()]
        return self._tally_client.list_collections()

    def _get_since_marker(self, collection_name: str) -> Optional[str]:
        """Get last sync marker for a collection from SQL state."""
        if self._state_manager is None:
            return None
        state = self._state_manager.get_state(collection_name)
        return state.source_marker if state else None

    def _sync_collection(
        self,
        collection_name: str,
        incremental: bool,
        since_marker: Optional[str],
    ) -> CollectionStats:
        """Sync a single collection: Tally -> Python batch -> SQL."""
        stats = CollectionStats(name=collection_name)
        start = time.monotonic()

        logger.info(
            "Syncing '%s' (incremental=%s, since_marker=%s)",
            collection_name, incremental, since_marker
        )

        if self._state_manager and not self.dry_run:
            self._state_manager.mark_running(collection_name, self.run_id)

        mapping = self._mapper.get(collection_name) if self._mapper else None
        if mapping is None:
            logger.warning("No mapping for '%s' - skipping.", collection_name)
            return stats

        new_marker: Optional[str] = since_marker
        batch_size = self.config.migration.batch_size
        batch: list[dict] = []
        error_occurred = False
        error_msg = ""

        try:
            # Select fetch method: full or incremental
            if incremental and since_marker:
                records_gen = self._tally_client.fetch_since(
                    collection_name, since_marker
                )
            else:
                records_gen = self._tally_client.fetch_all(collection_name)

            for raw_record in records_gen:
                stats.rows_read += 1
                # Transform
                transformed = self._transform_record(raw_record, mapping)
                if transformed:
                    batch.append(transformed)
                    # Track new change marker
                    marker_field = mapping.change_marker
                    if marker_field and marker_field in transformed:
                        candidate = str(transformed[marker_field] or "")
                        if candidate and (new_marker is None or candidate > new_marker):
                            new_marker = candidate

                # Flush batch when full
                if len(batch) >= batch_size:
                    batch_result = self._write_batch(batch, mapping)
                    stats.rows_inserted += batch_result.inserted
                    stats.rows_updated += batch_result.updated
                    stats.rows_unchanged += batch_result.unchanged
                    stats.rows_failed += batch_result.failed
                    batch = []
                    logger.debug(
                        "[%s] batch written: +%d rows read so far",
                        collection_name, stats.rows_read
                    )

            # Flush remaining
            if batch:
                batch_result = self._write_batch(batch, mapping)
                stats.rows_inserted += batch_result.inserted
                stats.rows_updated += batch_result.updated
                stats.rows_unchanged += batch_result.unchanged
                stats.rows_failed += batch_result.failed

        except Exception as exc:
            logger.exception("Error syncing '%s': %s", collection_name, exc)
            error_occurred = True
            error_msg = str(exc)
            stats.errors.append(error_msg)

        # Update sync state
        if self._state_manager and not self.dry_run:
            if error_occurred:
                self._state_manager.mark_failed(
                    collection_name, self.run_id, error_msg,
                    rows_read=stats.rows_read,
                    rows_inserted=stats.rows_inserted,
                    rows_failed=stats.rows_failed,
                )
            else:
                self._state_manager.mark_success(
                    collection_name, self.run_id,
                    source_marker=new_marker,
                    rows_read=stats.rows_read,
                    rows_inserted=stats.rows_inserted,
                    rows_updated=stats.rows_updated,
                    rows_deleted=stats.rows_deleted,
                    rows_failed=stats.rows_failed,
                )

        stats.duration_seconds = time.monotonic() - start
        logger.info(
            "[%s] done: read=%d inserted=%d updated=%d failed=%d in %.1fs",
            collection_name, stats.rows_read, stats.rows_inserted,
            stats.rows_updated, stats.rows_failed, stats.duration_seconds,
        )
        return stats

    def _transform_record(
        self, raw_record: dict, mapping
    ) -> Optional[dict]:
        """Transform a raw Tally record to SQL-ready dict."""
        from tally_migrator.migration.transforms import transform_record
        try:
            field_type_map = mapping.get_field_type_map()
            transformed = transform_record(
                raw_record=raw_record,
                field_type_map=field_type_map,
                collection=mapping.source_collection,
            )
            return mapping.to_sql_record(transformed)
        except Exception as exc:
            logger.error(
                "Transform failed for record in '%s': %s",
                mapping.source_collection, exc
            )
            return None

    def _write_batch(
        self, batch: list[dict], mapping
    ) -> "UpsertResult":  # type: ignore
        from tally_migrator.sql.upsert import BatchUpsertEngine, UpsertResult
        if self.dry_run or self._sql is None:
            return UpsertResult(inserted=len(batch))
        engine = BatchUpsertEngine(
            self._sql,
            mapping.schema_name,
            mapping.target_table,
            mapping.key_field or "guid",
            run_id=self.run_id,
        )
        return engine.upsert_batch(batch, dry_run=self.dry_run)
