"""Structured logging configuration and run-ID tracking."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_run_id: Optional[str] = None


def get_run_id() -> str:
    """Return the current run ID, creating one if needed."""
    global _run_id
    if _run_id is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        _run_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    return _run_id


def reset_run_id() -> str:
    """Generate and set a new run ID (call at the start of each command)."""
    global _run_id
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    _run_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    return _run_id


class RunIdFilter(logging.Filter):
    """Inject the current run_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.run_id = get_run_id()
        return True


def setup_logging(migration_cfg=None) -> None:  # type: ignore[type-arg]
    """Configure root logger with console + rotating file handler."""
    from tally_migrator.config import get_config

    if migration_cfg is None:
        migration_cfg = get_config().migration

    level_name = migration_cfg.log_level or "INFO"
    level = getattr(logging, level_name, logging.INFO)

    log_dir: Path = migration_cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    run_id = get_run_id()
    log_file = log_dir / f"run_{run_id}.log"

    fmt_str = "%(asctime)s [%(run_id)s] %(levelname)-8s %(name)s - %(message)s"
    formatter = logging.Formatter(fmt_str, datefmt="%Y-%m-%dT%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(formatter)
    ch.addFilter(RunIdFilter())
    root.addHandler(ch)

    # File handler (rotating)
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=10,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    fh.addFilter(RunIdFilter())
    root.addHandler(fh)

    # Silence noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pyodbc").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Migration result summary helpers
# ---------------------------------------------------------------------------


@dataclass
class CollectionStats:
    name: str
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    rows_deleted: int = 0
    rows_failed: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors or self.rows_failed > 0:
            return "FAILED"
        if self.rows_read == 0:
            return "EMPTY"
        return "OK"


@dataclass
class MigrationResult:
    run_id: str
    success: bool
    start_time: datetime
    end_time: Optional[datetime] = None
    dry_run: bool = False
    collections: list[CollectionStats] = field(default_factory=list)

    @property
    def duration(self) -> timedelta:
        end = self.end_time or datetime.now(timezone.utc)
        return end - self.start_time

    @property
    def total_read(self) -> int:
        return sum(c.rows_read for c in self.collections)

    @property
    def total_inserted(self) -> int:
        return sum(c.rows_inserted for c in self.collections)

    @property
    def total_updated(self) -> int:
        return sum(c.rows_updated for c in self.collections)

    @property
    def total_unchanged(self) -> int:
        return sum(c.rows_unchanged for c in self.collections)

    @property
    def total_deleted(self) -> int:
        return sum(c.rows_deleted for c in self.collections)

    @property
    def total_failed(self) -> int:
        return sum(c.rows_failed for c in self.collections)

    def print_summary(self) -> None:
        print()
        if self.dry_run:
            print("=" * 95)
            print("DRY RUN SUMMARY (no data written)")
        else:
            print("=" * 95)
            print("MIGRATION SUMMARY")
        print("=" * 95)
        print(f"Run ID : {self.run_id}")
        print(f"Status : {'SUCCESS' if self.success else 'FAILED'}")
        print(f"Started: {self.start_time.strftime('%Y-%m-%dT%H:%M:%S UTC')}")
        dur = self.duration.total_seconds()
        print(f"Duration: {dur:.1f}s")
        print()
        print(f"{'COLLECTION':<30} {'STATUS':<10} {'READ':>8} {'INSERTED':>10} {'UPDATED':>10} {'UNCHANGED':>10} {'FAILED':>8}")
        print("-" * 95)
        for c in self.collections:
            print(
                f"{c.name:<30} {c.status:<10} {c.rows_read:>8,} {c.rows_inserted:>10,} "
                f"{c.rows_updated:>10,} {c.rows_unchanged:>10,} {c.rows_failed:>8,}"
            )
        print("-" * 95)
        print(
            f"{'TOTAL':<35} {self.total_read:>8,} {self.total_inserted:>10,} "
            f"{self.total_updated:>10,} {self.total_unchanged:>10,} {self.total_failed:>8,}"
        )
        print()
        total_written = self.total_inserted + self.total_updated
        if dur > 0 and total_written > 0:
            print(f"Throughput: {total_written / dur:,.0f} rows/s")
        print()


class MigrationLogger(logging.LoggerAdapter):
    """Logger adapter that prefixes messages with collection and run context."""

    def __init__(self, logger: logging.Logger, collection: str = "", run_id: str = ""):
        extra = {"collection": collection, "run_id_override": run_id}
        super().__init__(logger, extra)

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        coll = self.extra.get("collection", "")
        prefix = f"[{coll}] " if coll else ""
        return f"{prefix}{msg}", kwargs
