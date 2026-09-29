"""Custom exception hierarchy for tally_migrator."""

from __future__ import annotations

from typing import Optional


class TallyMigratorError(Exception):
    """Base exception for all tally_migrator errors."""


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------

class ConfigurationError(TallyMigratorError):
    """Invalid or missing configuration."""


# ---------------------------------------------------------------------------
# Tally connectivity errors
# ---------------------------------------------------------------------------

class TallyConnectionError(TallyMigratorError):
    """Cannot connect to TallyPrime."""

    def __init__(self, host: str, port: int, detail: str = ""):
        self.host = host
        self.port = port
        self.detail = detail
        msg = f"Cannot connect to Tally at {host}:{port}"
        if detail:
            msg += f" - {detail}"
        super().__init__(msg)


class TallyQueryError(TallyMigratorError):
    """Error executing a Tally query."""

    def __init__(self, collection: str, detail: str):
        self.collection = collection
        super().__init__(f"Tally query failed for collection '{collection}': {detail}")


class TallyTimeoutError(TallyConnectionError):
    """Tally query or connection timed out."""


# ---------------------------------------------------------------------------
# SQL errors
# ---------------------------------------------------------------------------

class SQLConnectionError(TallyMigratorError):
    """Cannot connect to SQL Server."""

    def __init__(self, server: str, database: str, detail: str = ""):
        self.server = server
        self.database = database
        msg = f"Cannot connect to SQL Server {server}/{database}"
        if detail:
            msg += f" - {detail}"
        super().__init__(msg)


class SQLExecutionError(TallyMigratorError):
    """Error executing SQL statement."""

    def __init__(self, table: str, operation: str, detail: str):
        self.table = table
        self.operation = operation
        super().__init__(f"SQL error on {operation} into {table}: {detail}")


class SQLSchemaError(TallyMigratorError):
    """Schema-related SQL error."""


# ---------------------------------------------------------------------------
# Schema errors
# ---------------------------------------------------------------------------

class SchemaDiscoveryError(TallyMigratorError):
    """Error during Tally schema discovery."""


class SchemaValidationError(TallyMigratorError):
    """Discovered schema does not match approved SQL schema."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__(f"Schema validation failed with {len(issues)} issue(s):\n" + "\n".join(f"  - {i}" for i in issues))


class SchemaMappingError(TallyMigratorError):
    """A field or collection cannot be mapped."""

    def __init__(self, collection: str, field: str, detail: str):
        self.collection = collection
        self.field = field
        super().__init__(f"Mapping error in collection '{collection}', field '{field}': {detail}")


# ---------------------------------------------------------------------------
# Data transformation errors
# ---------------------------------------------------------------------------

class DataTransformError(TallyMigratorError):
    """Error transforming a Tally field value."""

    def __init__(
        self,
        collection: str,
        field: str,
        value: object,
        detail: str,
        record_id: Optional[str] = None,
    ):
        self.collection = collection
        self.field = field
        self.value = value
        self.record_id = record_id
        parts = [f"Transform error in '{collection}'.'{field}'",
                 f"value={value!r}", detail]
        if record_id:
            parts.append(f"record_id={record_id}")
        super().__init__(" | ".join(parts))


# ---------------------------------------------------------------------------
# Synchronisation errors
# ---------------------------------------------------------------------------

class SyncError(TallyMigratorError):
    """General synchronisation failure."""


class PartialSyncError(SyncError):
    """Some batches failed; partial data may have been written."""

    def __init__(self, collection: str, failed_batches: int, detail: str):
        self.collection = collection
        self.failed_batches = failed_batches
        super().__init__(
            f"Partial sync failure for '{collection}': {failed_batches} batch(es) failed - {detail}"
        )


class LockError(TallyMigratorError):
    """Another sync process is already running."""

    def __init__(self, lock_info: str = ""):
        msg = "Another synchronisation process is already running."
        if lock_info:
            msg += f" ({lock_info})"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

class TransientError(TallyMigratorError):
    """An error that may succeed if retried (network timeout, etc.)."""


class PermanentError(TallyMigratorError):
    """An error that will NOT succeed if retried (bad data, schema mismatch)."""
