"""Full synchronisation runner.

Full sync:
1. Acquires process lock (prevents concurrent runs)
2. Runs pipeline for ALL collections (or configured subset)
3. Releases lock on completion or failure
"""

from __future__ import annotations

import logging

from tally_migrator.logging.logger import MigrationResult
from tally_migrator.migration.pipeline import MigrationPipeline

logger = logging.getLogger(__name__)


class FullSyncRunner:
    """Runs a full Tally -> SQL synchronisation."""

    def __init__(self, config, run_id: str):
        self.config = config
        self.run_id = run_id

    def run(self) -> MigrationResult:
        logger.info("FullSyncRunner starting run_id=%s", self.run_id)
        pipeline = MigrationPipeline(
            config=self.config,
            run_id=self.run_id,
            dry_run=self.config.migration.dry_run,
        )
        result = pipeline.run_full_sync()
        return result
