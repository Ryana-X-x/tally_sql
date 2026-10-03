"""CLI entry point for tally_migrator.

Usage:
    python -m tally_migrator <command> [options]

Commands:
    test-connection   Verify Tally and SQL Server connectivity.
    discover          Discover Tally schema and persist to schema/discovered_schema.json.
    validate-schema   Compare discovered schema against current SQL schema.
    generate-schema   Generate SQL DDL from approved schema.
    dry-run           Connect, transform, validate - but do NOT write to SQL.
    full-sync         Full data migration: Tally → SQL Server.
    incremental-sync  Incremental synchronisation using change markers.
    status            Show synchronisation status for all collections.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from tally_migrator import __version__
from tally_migrator.config import get_config, load_config
from tally_migrator.logging.logger import get_run_id, setup_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command implementations (import lazily to keep CLI fast)
# ---------------------------------------------------------------------------

def _cmd_test_connection(args: argparse.Namespace) -> int:
    from tally_migrator.sql.connection import SQLConnection
    from tally_migrator.tally.factory import create_tally_client

    cfg = get_config()
    errors = 0

    # --- Tally ---
    print("\n[1/2] Testing Tally connection ...")
    try:
        client = create_tally_client(cfg.tally)
        info = client.test_connection()
        print(f"  ✓ Tally reachable  ({cfg.tally.protocol.upper()} @ {cfg.tally.host}:{cfg.tally.port})")
        if info:
            for k, v in info.items():
                print(f"      {k}: {v}")
    except Exception as exc:
        print(f"  ✗ Tally connection FAILED: {exc}")
        errors += 1

    # --- SQL Server ---
    print("\n[2/2] Testing SQL Server connection ...")
    try:
        sql = SQLConnection(cfg.sql)
        sql.test_connection()
        print(f"  ✓ SQL Server reachable  ({cfg.sql.server}/{cfg.sql.database})")
        sql.close()
    except Exception as exc:
        print(f"  ✗ SQL Server connection FAILED: {exc}")
        errors += 1

    if errors == 0:
        print("\nAll connections OK.")
        return 0
    print(f"\n{errors} connection(s) FAILED.")
    return 1


def _cmd_discover(args: argparse.Namespace) -> int:
    from tally_migrator.schema.discovery import SchemaDiscovery
    from tally_migrator.tally.factory import create_tally_client

    cfg = get_config()
    print("Starting Tally schema discovery ...")
    client = create_tally_client(cfg.tally)
    discovery = SchemaDiscovery(client, cfg)
    schema = discovery.discover()
    out_path = cfg.migration.schema_dir / "discovered_schema.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    discovery.save(schema, out_path)
    print(f"\nDiscovery complete. Schema written to: {out_path}")
    print(f"Collections found: {len(schema.collections)}")
    for name, coll in sorted(schema.collections.items()):
        field_count = len(coll.fields)
        print(f"  {name:<40} {field_count} fields")
    return 0


def _cmd_validate_schema(args: argparse.Namespace) -> int:
    from tally_migrator.schema.validator import SchemaValidator
    from tally_migrator.sql.connection import SQLConnection

    cfg = get_config()
    schema_path = cfg.migration.schema_dir / "discovered_schema.json"
    if not schema_path.exists():
        print(f"ERROR: Discovered schema not found at {schema_path}")
        print("Run:  python -m tally_migrator discover  first.")
        return 1

    sql = SQLConnection(cfg.sql)
    validator = SchemaValidator(schema_path, sql, cfg)
    result = validator.validate()
    result.print_report()
    sql.close()
    return 0 if result.is_valid else 1


def _cmd_generate_schema(args: argparse.Namespace) -> int:
    from tally_migrator.schema.generator import SQLSchemaGenerator

    cfg = get_config()
    schema_path = cfg.migration.schema_dir / "discovered_schema.json"
    if not schema_path.exists():
        print(f"ERROR: Discovered schema not found at {schema_path}")
        print("Run:  python -m tally_migrator discover  first.")
        return 1

    gen = SQLSchemaGenerator(schema_path, cfg)
    ddl = gen.generate()
    out_path = cfg.migration.sql_dir / "schema.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(ddl, encoding="utf-8")
    print(f"SQL schema written to: {out_path}")

    if args.apply:
        from tally_migrator.sql.connection import SQLConnection
        sql = SQLConnection(cfg.sql)
        print("Applying schema to SQL Server ...")
        sql.execute_script(ddl)
        sql.close()
        print("Schema applied.")
    else:
        print("Schema NOT applied (use --apply to apply to SQL Server).")
    return 0


def _cmd_dry_run(args: argparse.Namespace) -> int:
    from tally_migrator.migration.pipeline import MigrationPipeline

    cfg = get_config()
    cfg.migration.dry_run = True
    print("DRY RUN - no data will be written to SQL Server.")
    run_id = get_run_id()
    pipeline = MigrationPipeline(cfg, run_id=run_id, dry_run=True)
    result = pipeline.run_full_sync()
    result.print_summary()
    return 0 if result.success else 1


def _cmd_full_sync(args: argparse.Namespace) -> int:
    from tally_migrator.migration.full_sync import FullSyncRunner

    cfg = get_config()
    run_id = get_run_id()
    print(f"Starting FULL SYNC  run_id={run_id}")
    runner = FullSyncRunner(cfg, run_id=run_id)
    result = runner.run()
    result.print_summary()
    return 0 if result.success else 1


def _cmd_incremental_sync(args: argparse.Namespace) -> int:
    from tally_migrator.migration.incremental_sync import IncrementalSyncRunner

    cfg = get_config()
    run_id = get_run_id()
    print(f"Starting INCREMENTAL SYNC  run_id={run_id}")
    runner = IncrementalSyncRunner(cfg, run_id=run_id)
    result = runner.run()
    result.print_summary()
    return 0 if result.success else 1


def _cmd_status(args: argparse.Namespace) -> int:
    from tally_migrator.sql.connection import SQLConnection
    from tally_migrator.sql.sync_state import SyncStateManager

    cfg = get_config()
    sql = SQLConnection(cfg.sql)
    mgr = SyncStateManager(sql, cfg)
    states = mgr.get_all_states()
    if not states:
        print("No synchronisation state found. Run full-sync first.")
        sql.close()
        return 0

    print(f"\n{'COLLECTION':<35} {'LAST SYNC':<25} {'STATUS':<12} {'INSERTED':>10} {'UPDATED':>10} {'FAILED':>8}")
    print("-" * 105)
    for state in states:
        last = state.last_successful_sync.strftime("%Y-%m-%d %H:%M:%S") if state.last_successful_sync else "never"
        print(
            f"{state.collection_name:<35} "
            f"{last:<25} "
            f"{state.status:<12} "
            f"{state.rows_inserted:>10,} "
            f"{state.rows_updated:>10,} "
            f"{state.rows_failed:>8,}"
        )
    sql.close()
    return 0


def _cmd_catalog(args: argparse.Namespace) -> int:
    from tally_migrator.schema.catalog import DataCatalogGenerator

    cfg = get_config()
    print("Generating Tally Data Catalog ...")
    gen = DataCatalogGenerator(cfg.migration.schema_dir)
    cat = gen.generate()
    print(f"Data catalog generated at: {gen.catalog_path}")
    print(f"Total collections cataloged: {cat['collections_count']}")
    return 0


def _cmd_coverage(args: argparse.Namespace) -> int:
    from tally_migrator.reports.coverage import CoverageReporter
    from tally_migrator.sql.connection import SQLConnection

    cfg = get_config()
    sql = None
    try:
        sql = SQLConnection(cfg.sql)
    except Exception:
        pass

    reporter = CoverageReporter(cfg, sql_conn=sql)
    report = reporter.generate_report()
    print(report)
    if sql:
        sql.close()
    return 0


def _cmd_reconcile(args: argparse.Namespace) -> int:
    from tally_migrator.reports.reconcile import Reconciler
    from tally_migrator.sql.connection import SQLConnection

    cfg = get_config()
    sql = None
    try:
        sql = SQLConnection(cfg.sql)
    except Exception:
        pass

    reconciler = Reconciler(cfg, sql_conn=sql)
    report = reconciler.reconcile()
    print(report)
    if sql:
        sql.close()
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tally_migrator",
        description="TallyPrime → SQL Server migration and synchronisation tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m tally_migrator test-connection\n"
            "  python -m tally_migrator discover\n"
            "  python -m tally_migrator catalog\n"
            "  python -m tally_migrator coverage\n"
            "  python -m tally_migrator reconcile\n"
            "  python -m tally_migrator generate-schema --apply\n"
            "  python -m tally_migrator full-sync\n"
            "  python -m tally_migrator incremental-sync\n"
            "  python -m tally_migrator status\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"tally_migrator {__version__}")
    parser.add_argument("--env", metavar="FILE", help="Path to .env file (default: auto-detect)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Override log level")
    parser.add_argument("--log-dir", metavar="DIR", help="Override log directory")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # test-connection
    sub.add_parser("test-connection", help="Test Tally and SQL Server connectivity")

    # discover
    sub.add_parser("discover", help="Discover Tally collections and schema metadata")

    # catalog
    sub.add_parser("catalog", help="Generate persistent data_catalog.json metadata artifact")

    # coverage
    sub.add_parser("coverage", help="Display Tally data coverage report")

    # reconcile
    sub.add_parser("reconcile", help="Display source vs SQL target count reconciliation report")

    # validate-schema
    sub.add_parser("validate-schema", help="Validate discovered schema against SQL Server")

    # generate-schema
    gen = sub.add_parser("generate-schema", help="Generate SQL DDL from discovered schema")
    gen.add_argument("--apply", action="store_true", help="Apply generated DDL to SQL Server")

    # dry-run
    sub.add_parser("dry-run", help="Run migration pipeline without writing to SQL")

    # full-sync
    full = sub.add_parser("full-sync", help="Perform full data synchronisation")
    full.add_argument("--collections", nargs="+", metavar="COLLECTION",
                      help="Limit to specific collections (default: all)")
    full.add_argument("--batch-size", type=int, help="Override BATCH_SIZE")

    # incremental-sync
    inc = sub.add_parser("incremental-sync", help="Perform incremental synchronisation")
    inc.add_argument("--collections", nargs="+", metavar="COLLECTION",
                     help="Limit to specific collections")

    # status
    sub.add_parser("status", help="Show synchronisation status for all collections")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Load config
    env_file = Path(args.env) if args.env else None
    cfg = load_config(env_file)

    # Override log settings from CLI args
    if args.log_level:
        cfg.migration.log_level = args.log_level
    if args.log_dir:
        cfg.migration.log_dir = Path(args.log_dir)

    # Apply CLI overrides for full-sync / incremental-sync
    if args.command in ("full-sync", "incremental-sync"):
        if hasattr(args, "collections") and args.collections:
            cfg.migration.collections = args.collections
        if hasattr(args, "batch_size") and args.batch_size:
            cfg.migration.batch_size = args.batch_size

    # Setup logging
    setup_logging(cfg.migration)
    logger.debug("Configuration loaded.")

    # Validate config (non-fatal warnings)
    issues = cfg.validate()
    if issues and args.command not in ("status",):
        for issue in issues:
            logger.warning("Config issue: %s", issue)
        if args.command not in ("test-connection",):
            # Still allow the command to run; individual handlers will raise on real error
            pass

    # Dispatch
    handlers = {
        "test-connection": _cmd_test_connection,
        "discover": _cmd_discover,
        "catalog": _cmd_catalog,
        "coverage": _cmd_coverage,
        "reconcile": _cmd_reconcile,
        "validate-schema": _cmd_validate_schema,
        "generate-schema": _cmd_generate_schema,
        "dry-run": _cmd_dry_run,
        "full-sync": _cmd_full_sync,
        "incremental-sync": _cmd_incremental_sync,
        "status": _cmd_status,
    }

    start = time.monotonic()
    try:
        exit_code = handlers[args.command](args)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        exit_code = 130
    except Exception as exc:
        logger.exception("Unhandled error in command '%s': %s", args.command, exc)
        print(f"\nFATAL ERROR: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        elapsed = time.monotonic() - start
        logger.info("Command '%s' finished in %.1f s (exit=%s)", args.command, elapsed, exit_code)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
