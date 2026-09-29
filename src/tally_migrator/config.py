"""Centralised configuration management via environment variables and .env file.

All secrets are read from the environment - never hardcoded.
Call load_config() once at startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional dotenv support
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False


def _load_env_file(env_file: Optional[Path] = None) -> None:
    """Load .env if dotenv is available."""
    if not _HAS_DOTENV:
        return
    if env_file is None:
        # Walk up from cwd to find .env
        candidate = Path.cwd()
        for _ in range(5):
            dot = candidate / ".env"
            if dot.exists():
                _load_dotenv(dot, override=False)
                return
            candidate = candidate.parent
    else:
        if env_file.exists():
            _load_dotenv(env_file, override=False)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TallyConfig:
    host: str = "localhost"
    port: int = 9000
    protocol: str = "odbc"          # 'odbc' or 'xml'
    company_name: str = ""          # optional filter
    timeout_seconds: int = 30
    xml_encoding: str = "utf-8"
    odbc_dsn: str = ""              # explicit DSN if needed
    odbc_driver: str = "Tally ODBC Driver"

    @classmethod
    def from_env(cls) -> "TallyConfig":
        host = _env("TALLY_HOST", "localhost")
        port = _env_int("TALLY_PORT", 9000)
        tally_url = _env("TALLY_URL", "")
        if tally_url:
            from urllib.parse import urlparse
            url_to_parse = tally_url if "://" in tally_url else f"http://{tally_url}"
            parsed = urlparse(url_to_parse)
            if parsed.hostname:
                host = parsed.hostname
            if parsed.port:
                port = parsed.port

        return cls(
            host=host,
            port=port,
            protocol=_env("TALLY_PROTOCOL", "xml").lower(),
            company_name=_env("TALLY_COMPANY", ""),
            timeout_seconds=_env_int("TALLY_TIMEOUT", 30),
            xml_encoding=_env("TALLY_XML_ENCODING", "utf-8"),
            odbc_dsn=_env("TALLY_ODBC_DSN", ""),
            odbc_driver=_env("TALLY_ODBC_DRIVER", "Tally ODBC Driver"),
        )

    @property
    def xml_base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class SQLConfig:
    server: str = "localhost"
    database: str = "TallyDB"
    username: str = ""
    password: str = ""
    driver: str = "ODBC Driver 17 for SQL Server"
    port: int = 1433
    trusted_connection: bool = False
    connection_timeout: int = 30
    command_timeout: int = 300
    encrypt: bool = True
    trust_server_certificate: bool = False
    schema_name: str = "dbo"        # target SQL schema

    @classmethod
    def from_env(cls) -> "SQLConfig":
        username = _env("SQL_USER", "") or _env("SQL_USERNAME", "")
        return cls(
            server=_env("SQL_SERVER", "localhost"),
            database=_env("SQL_DATABASE", "TallyDB"),
            username=username,
            password=_env("SQL_PASSWORD", ""),
            driver=_env("SQL_DRIVER", "ODBC Driver 17 for SQL Server"),
            port=_env_int("SQL_PORT", 1433),
            trusted_connection=_env_bool("SQL_TRUSTED_CONNECTION", False),
            connection_timeout=_env_int("SQL_CONNECTION_TIMEOUT", 30),
            command_timeout=_env_int("SQL_COMMAND_TIMEOUT", 300),
            encrypt=_env_bool("SQL_ENCRYPT", True),
            trust_server_certificate=_env_bool("SQL_TRUST_SERVER_CERT", False),
            schema_name=_env("SQL_SCHEMA", "dbo"),
        )

    def connection_string(self, *, hide_password: bool = False) -> str:
        """Build pyodbc connection string. Never log with hide_password=False."""
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server},{self.port}",
            f"DATABASE={self.database}",
        ]
        if self.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.username}")
            pwd = "***" if hide_password else self.password
            parts.append(f"PWD={pwd}")
        parts.append(f"Connection Timeout={self.connection_timeout}")
        parts.append(f"Encrypt={'yes' if self.encrypt else 'no'}")
        parts.append(f"TrustServerCertificate={'yes' if self.trust_server_certificate else 'no'}")
        return ";".join(parts)


@dataclass
class MigrationConfig:
    batch_size: int = 5000
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    dry_run: bool = False
    collections: list = field(default_factory=list)   # empty = all
    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    schema_dir: Path = Path("schema")
    sql_dir: Path = Path("sql")

    @classmethod
    def from_env(cls) -> "MigrationConfig":
        log_dir = Path(_env("LOG_DIR", "logs"))
        schema_dir = Path(_env("SCHEMA_DIR", "schema"))
        sql_dir = Path(_env("SQL_DIR", "sql"))
        collections_raw = _env("COLLECTIONS", "")
        collections = [c.strip() for c in collections_raw.split(",") if c.strip()] if collections_raw else []
        return cls(
            batch_size=_env_int("BATCH_SIZE", 5000),
            max_retries=_env_int("MAX_RETRIES", 3),
            retry_backoff_seconds=_env_float("RETRY_BACKOFF_SECONDS", 2.0),
            dry_run=_env_bool("DRY_RUN", False),
            collections=collections,
            log_level=_env("LOG_LEVEL", "INFO").upper(),
            log_dir=log_dir,
            schema_dir=schema_dir,
            sql_dir=sql_dir,
        )


@dataclass
class AppConfig:
    tally: TallyConfig = field(default_factory=TallyConfig)
    sql: SQLConfig = field(default_factory=SQLConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "AppConfig":
        _load_env_file(env_file)
        return cls(
            tally=TallyConfig.from_env(),
            sql=SQLConfig.from_env(),
            migration=MigrationConfig.from_env(),
        )

    def validate(self) -> list[str]:
        """Return list of configuration problems (empty = valid)."""
        issues: list[str] = []
        if not self.tally.host:
            issues.append("TALLY_HOST is not set")
        if self.tally.protocol not in ("odbc", "xml"):
            issues.append(f"TALLY_PROTOCOL must be 'odbc' or 'xml', got: {self.tally.protocol!r}")
        if not self.sql.server:
            issues.append("SQL_SERVER is not set")
        if not self.sql.database:
            issues.append("SQL_DATABASE is not set")
        if not self.sql.trusted_connection:
            if not self.sql.username:
                issues.append("SQL_USERNAME is not set (and SQL_TRUSTED_CONNECTION is not enabled)")
            if not self.sql.password:
                issues.append("SQL_PASSWORD is not set (and SQL_TRUSTED_CONNECTION is not enabled)")
        if self.migration.batch_size < 1:
            issues.append("BATCH_SIZE must be >= 1")
        return issues


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_config: Optional[AppConfig] = None


def load_config(env_file: Optional[Path] = None) -> AppConfig:
    """Load and cache configuration. Call once at application startup."""
    global _config
    _config = AppConfig.from_env(env_file)
    return _config


def get_config() -> AppConfig:
    """Return the cached configuration, loading it if necessary."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
