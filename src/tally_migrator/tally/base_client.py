"""Abstract base class for Tally client implementations.

Both ODBCClient and XMLClient implement this interface.
The migration pipeline uses only this interface - it has no
knowledge of the transport mechanism.
"""

from __future__ import annotations

import abc
from typing import Any, Generator, Optional


class BaseTallyClient(abc.ABC):
    """Abstract Tally client. All transport-specific clients must inherit from this."""

    def __init__(self, config):
        self.config = config

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def connect(self) -> None:
        """Establish connection to Tally. Raise TallyConnectionError on failure."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release all resources."""

    def __enter__(self) -> "BaseTallyClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Return a dict of connection info, e.g. {'server_version': '...'}"""

    # ------------------------------------------------------------------
    # Collection discovery
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_collections(self) -> list[str]:
        """Return names of all queryable collections."""

    @abc.abstractmethod
    def describe_collection(self, collection_name: str) -> dict[str, Any]:
        """Return metadata dict for the given collection.

        Expected keys:
            name        str
            fields      list[dict]   each: {name, type, sample}
            row_count   int (best effort)
        """

    # ------------------------------------------------------------------
    # Data retrieval
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def fetch_all(
        self,
        collection_name: str,
        fields: Optional[list[str]] = None,
        filters: Optional[dict[str, Any]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield all records from a collection as plain dicts.

        This is the primary data retrieval interface.
        Implementations should yield one record at a time (not load all into memory).

        Args:
            collection_name: Tally collection name, e.g. 'Ledger'.
            fields: List of field names to retrieve (None = all available).
            filters: Optional filter criteria (implementation-specific).
            company: Company context (overrides config.company_name if set).
        """

    @abc.abstractmethod
    def fetch_since(
        self,
        collection_name: str,
        since_marker: Optional[str],
        fields: Optional[list[str]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield records modified since the given marker (for incremental sync).

        Args:
            collection_name: Tally collection name.
            since_marker: Opaque marker from last sync (date string, GUID, etc.).
                         If None, yield all records.
            fields: List of field names to retrieve.
            company: Company context.
        """

    @abc.abstractmethod
    def count_records(
        self,
        collection_name: str,
        company: Optional[str] = None,
    ) -> int:
        """Return approximate row count for a collection."""

    # ------------------------------------------------------------------
    # Company information
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def list_companies(self) -> list[str]:
        """Return list of available company names in Tally."""

    @abc.abstractmethod
    def get_company_info(self, company: Optional[str] = None) -> dict[str, Any]:
        """Return metadata about the selected company."""
