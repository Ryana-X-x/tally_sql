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
        self._lock = None

    def _setup(self) -> None:
        """Initialise clients, lock, and mapping."""

        from tally_migrator.sql.connection import SQLConnection
        from tally_migrator.sql.lock import SQLProcessLock
        from tally_migrator.sql.sync_state import SyncStateManager
        from tally_migrator.tally.factory import create_tally_client

        self._tally_client = create_tally_client(self.config.tally)
        self._tally_client.connect()

        if not self.dry_run:
            self._sql = SQLConnection(self.config.sql)
            self._state_manager = SyncStateManager(self._sql, self.config)
            self._lock = SQLProcessLock(self._sql, self.config)
            self._lock.acquire(run_id=self.run_id)

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
        if self._lock:
            try:
                self._lock.release()
            except Exception as exc:
                logger.warning("Error releasing process lock: %s", exc)
            self._lock = None
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

        last_successful_marker: Optional[str] = since_marker
        batch_high_marker: Optional[str] = since_marker
        batch_size = self.config.migration.batch_size
        batch: list[tuple[dict, dict]] = []  # list of (raw_record, transformed_record)
        error_occurred = False
        error_msg = ""

        try:
            if incremental and since_marker:
                records_gen = self._tally_client.fetch_since(
                    collection_name, since_marker
                )
            else:
                records_gen = self._tally_client.fetch_all(collection_name)

            for raw_record in records_gen:
                stats.rows_read += 1
                transformed = self._transform_record(raw_record, mapping)
                if transformed:
                    batch.append((raw_record, transformed))
                    marker_field = mapping.change_marker
                    if marker_field and marker_field in transformed:
                        candidate = str(transformed[marker_field] or "")
                        if candidate and _is_higher_marker(candidate, batch_high_marker):
                            batch_high_marker = candidate

                # Flush batch when full
                if len(batch) >= batch_size:
                    batch_records = [t for _, t in batch]
                    batch_result = self._write_batch(batch_records, mapping)
                    stats.rows_inserted += batch_result.inserted
                    stats.rows_updated += batch_result.updated
                    stats.rows_unchanged += batch_result.unchanged
                    stats.rows_failed += batch_result.failed

                    if collection_name.lower() == "voucher":
                        self._sync_voucher_children(batch)

                    if batch_result.failed == 0:
                        last_successful_marker = batch_high_marker
                    else:
                        error_occurred = True
                        error_msg = f"{batch_result.failed} rows failed in batch"

                    batch = []

            # Flush remaining
            if batch:
                batch_records = [t for _, t in batch]
                batch_result = self._write_batch(batch_records, mapping)
                stats.rows_inserted += batch_result.inserted
                stats.rows_updated += batch_result.updated
                stats.rows_unchanged += batch_result.unchanged
                stats.rows_failed += batch_result.failed

                if collection_name.lower() == "voucher":
                    self._sync_voucher_children(batch)

                if batch_result.failed == 0 and not error_occurred:
                    last_successful_marker = batch_high_marker
                elif batch_result.failed > 0:
                    error_occurred = True
                    error_msg = f"{batch_result.failed} rows failed in final batch"

        except Exception as exc:
            logger.exception("Error syncing '%s': %s", collection_name, exc)
            error_occurred = True
            error_msg = str(exc)
            stats.errors.append(error_msg)

        # Update sync state - never advance state past failed records
        if self._state_manager and not self.dry_run:
            if error_occurred or stats.rows_failed > 0:
                self._state_manager.mark_failed(
                    collection_name, self.run_id, error_msg or "Sync encountered failed records",
                    rows_read=stats.rows_read,
                    rows_inserted=stats.rows_inserted,
                    rows_failed=stats.rows_failed,
                )
            else:
                self._state_manager.mark_success(
                    collection_name, self.run_id,
                    source_marker=last_successful_marker,
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

    def _sync_voucher_children(self, batch: list[tuple[dict, dict]]) -> None:
        """Extract and persist voucher leg & allocation sub-collections."""
        if self.dry_run or self._sql is None:
            return

        from tally_migrator.migration.transforms import (
            transform_amount,
            transform_boolean,
            transform_date,
            transform_quantity,
            transform_text,
        )
        from tally_migrator.sql.upsert import BatchUpsertEngine

        voucher_guids: list[str] = []
        ledger_entries: list[dict] = []
        inventory_entries: list[dict] = []
        bill_allocations: list[dict] = []
        bank_entries: list[dict] = []
        batch_allocations: list[dict] = []

        for raw, transformed in batch:
            v_guid = transformed.get("guid")
            if not v_guid:
                continue
            voucher_guids.append(v_guid)

            # Ledger legs
            raw_legs = raw.get("ALLLEDGERENTRIES.LIST") or raw.get("LEDGERENTRIES.LIST") or []
            if isinstance(raw_legs, dict):
                raw_legs = [raw_legs]
            for leg in raw_legs:
                if isinstance(leg, dict):
                    ledger_entries.append({
                        "voucher_guid": v_guid,
                        "ledger_name": str(leg.get("LEDGERNAME") or leg.get("NAME") or ""),
                        "amount": transform_amount(leg.get("AMOUNT")),
                        "is_party": transform_boolean(leg.get("ISPARTYLEDGER")),
                        "currency_name": transform_text(leg.get("CURRENCYNAME")),
                        "forex_amount": transform_amount(leg.get("FOREXAMOUNT")),
                        "entry_type": transform_text(leg.get("ENTRYTYPE")),
                        "gst_class": transform_text(leg.get("GSTCLASS")),
                    })

            # Inventory legs
            raw_inv = raw.get("ALLINVENTORYENTRIES.LIST") or raw.get("INVENTORYENTRIES.LIST") or []
            if isinstance(raw_inv, dict):
                raw_inv = [raw_inv]
            for inv in raw_inv:
                if isinstance(inv, dict):
                    inventory_entries.append({
                        "voucher_guid": v_guid,
                        "stock_item_name": str(inv.get("STOCKITEMNAME") or ""),
                        "quantity": transform_quantity(inv.get("ACTUALQTY") or inv.get("BILLEDQTY") or inv.get("QUANTITY")),
                        "rate": transform_amount(inv.get("RATE")),
                        "amount": transform_amount(inv.get("AMOUNT")),
                        "uom": transform_text(inv.get("UOM")),
                        "godown_name": transform_text(inv.get("GODOWNNAME")),
                        "batch_name": transform_text(inv.get("BATCHNAME")),
                        "tracking_number": transform_text(inv.get("TRACKINGNUMBER")),
                    })

            # Bill allocations
            raw_bills = raw.get("BILLALLOCATIONS.LIST") or []
            if isinstance(raw_bills, dict):
                raw_bills = [raw_bills]
            for bill in raw_bills:
                if isinstance(bill, dict):
                    bill_allocations.append({
                        "voucher_guid": v_guid,
                        "bill_name": str(bill.get("NAME") or ""),
                        "bill_type": transform_text(bill.get("BILLTYPE")),
                        "amount": transform_amount(bill.get("AMOUNT")),
                        "due_date": transform_date(bill.get("DUEDATE")),
                    })

            # Bank entries
            raw_bank = raw.get("BANKALLOCATIONS.LIST") or []
            if isinstance(raw_bank, dict):
                raw_bank = [raw_bank]
            for b in raw_bank:
                if isinstance(b, dict):
                    bank_entries.append({
                        "voucher_guid": v_guid,
                        "instrument_date": transform_date(b.get("INSTRUMENTDATE")),
                        "instrument_number": transform_text(b.get("INSTRUMENTNUMBER")),
                        "bank_name": transform_text(b.get("BANKNAME")),
                        "amount": transform_amount(b.get("AMOUNT")),
                    })

            # Batch allocations
            raw_batches = raw.get("BATCHALLOCATIONS.LIST") or []
            if isinstance(raw_batches, dict):
                raw_batches = [raw_batches]
            for ba in raw_batches:
                if isinstance(ba, dict):
                    batch_allocations.append({
                        "voucher_guid": v_guid,
                        "batch_name": transform_text(ba.get("BATCHNAME")),
                        "destination_godown": transform_text(ba.get("DESTINATIONGODOWN")),
                        "quantity": transform_quantity(ba.get("ACTUALQTY") or ba.get("QUANTITY")),
                        "amount": transform_amount(ba.get("AMOUNT")),
                        "manufactured_on": transform_date(ba.get("MFDON")),
                        "expiry_period": transform_text(ba.get("EXPIRYPERIOD")),
                    })

        if not voucher_guids:
            return

        schema_name = self.config.sql.schema_name
        sub_tables = [
            ("voucher_ledger_entry", ledger_entries),
            ("voucher_inventory_entry", inventory_entries),
            ("voucher_bill_allocation", bill_allocations),
            ("voucher_bank_entry", bank_entries),
            ("voucher_batch_allocation", batch_allocations),
        ]

        # Purge existing sub-entries for these vouchers and bulk insert fresh ones
        guids_str = ", ".join(f"'{g}'" for g in voucher_guids)
        cursor = self._sql.cursor()
        try:
            for tbl_name, entries in sub_tables:
                if not entries:
                    continue
                try:
                    cursor.execute(f"DELETE FROM [{schema_name}].[{tbl_name}] WHERE [voucher_guid] IN ({guids_str})")
                    self._sql.commit()
                except Exception as exc:
                    logger.debug("Clean sub-table %s delete skipped: %s", tbl_name, exc)

                engine = BatchUpsertEngine(self._sql, schema_name, tbl_name, "id", run_id=self.run_id)
                engine.bulk_insert(entries, dry_run=self.dry_run)
            cursor.close()
        except Exception as exc:
            cursor.close()
            logger.error("Failed persisting voucher child collections: %s", exc)

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


def _is_higher_marker(candidate: str, current: Optional[str]) -> bool:
    """Compare change markers numerically (e.g. 1000 > 99) with string fallback."""
    if current is None or current == "":
        return True
    if not candidate:
        return False
    try:
        return int(candidate) > int(current)
    except (ValueError, TypeError):
        try:
            return float(candidate) > float(current)
        except (ValueError, TypeError):
            return candidate > current
