"""Factory for creating the appropriate Tally client based on configuration."""

from __future__ import annotations

from tally_migrator.exceptions import ConfigurationError
from tally_migrator.tally.base_client import BaseTallyClient


def create_tally_client(tally_config) -> BaseTallyClient:
    """Create and return the appropriate Tally client.

    Args:
        tally_config: TallyConfig instance.

    Returns:
        BaseTallyClient subclass (XMLTallyClient or ODBCTallyClient).

    Raises:
        ConfigurationError: If protocol is unrecognised.
    """
    protocol = tally_config.protocol.lower()

    if protocol == "xml":
        from tally_migrator.tally.xml_client import XMLTallyClient
        return XMLTallyClient(tally_config)

    elif protocol == "odbc":
        from tally_migrator.tally.odbc_client import ODBCTallyClient
        return ODBCTallyClient(tally_config)

    else:
        raise ConfigurationError(
            f"Unknown TALLY_PROTOCOL: '{protocol}'. "
            f"Valid values: 'xml', 'odbc'"
        )
