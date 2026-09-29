"""Data transformation functions for Tally -> SQL Server.

All transformations are explicit and type-safe.
Never rely on SQL Server implicit conversion.

Critical: date handling is explicit (Tally date bug history).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from tally_migrator.exceptions import DataTransformError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tally date format constants
# ---------------------------------------------------------------------------
# Tally dates are typically YYYYMMDD (e.g. 20231201)
# Some contexts use DD/MM/YYYY or YYYY-MM-DD

TALLY_DATE_FORMATS = [
    "%Y%m%d",       # 20231201 (most common)
    "%Y-%m-%d",     # 2023-12-01
    "%d/%m/%Y",     # 01/12/2023
    "%d-%m-%Y",     # 01-12-2023
    "%m/%d/%Y",     # 12/01/2023 (rare)
]

# Tally uses this to represent 'no date' / epoch
TALLY_NULL_DATES = {"19000101", "00000000", "", "0", "00/00/0000"}
TALLY_EPOCH = date(1900, 1, 1)

# ---------------------------------------------------------------------------
# Boolean values from Tally XML/ODBC
# ---------------------------------------------------------------------------

TALLY_TRUE_VALUES = {"yes", "true", "1", "on", "y"}
TALLY_FALSE_VALUES = {"no", "false", "0", "off", "n", ""}


# ---------------------------------------------------------------------------
# Core transformation functions
# ---------------------------------------------------------------------------

def transform_text(
    value: Any,
    collection: str = "",
    field: str = "",
    max_length: Optional[int] = None,
    record_id: Optional[str] = None,
) -> Optional[str]:
    """Transform a Tally text value to a Python str or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if max_length and len(s) > max_length:
        logger.debug(
            "Field '%s.%s' truncated from %d to %d chars (record %s)",
            collection, field, len(s), max_length, record_id,
        )
        s = s[:max_length]
    return s


def transform_date(
    value: Any,
    collection: str = "",
    field: str = "",
    record_id: Optional[str] = None,
    strict: bool = False,
) -> Optional[date]:
    """Transform a Tally date value to a Python date.

    Handles:
    - YYYYMMDD (Tally native format)
    - YYYY-MM-DD
    - DD/MM/YYYY
    - None / empty / null sentinel values -> None

    Args:
        strict: If True, raise DataTransformError on unrecognised format.
                If False (default), log a warning and return None.
    """
    if value is None:
        return None

    raw = str(value).strip()

    if raw in TALLY_NULL_DATES:
        return None

    # Handle Tally epoch date (no meaningful date)
    for fmt in TALLY_DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt).date()
            # Treat Tally epoch as null
            if parsed == TALLY_EPOCH:
                return None
            # Sanity check: reject obviously invalid dates
            if parsed.year < 1900 or parsed.year > 2100:
                logger.warning(
                    "Suspicious year %d in '%s.%s' value=%r (record=%s)",
                    parsed.year, collection, field, raw, record_id,
                )
                return None
            return parsed
        except ValueError:
            continue

    # If we get here, format was not recognised
    msg = f"Unrecognised date format: {raw!r}"
    if strict:
        raise DataTransformError(collection, field, value, msg, record_id)
    else:
        logger.warning(
            "Cannot parse date in '%s.%s': %r (record=%s) - using NULL",
            collection, field, raw, record_id,
        )
        return None


def transform_amount(
    value: Any,
    collection: str = "",
    field: str = "",
    record_id: Optional[str] = None,
) -> Optional[Decimal]:
    """Transform a Tally amount to Decimal.

    Tally amounts may be:
    - Plain numeric: "12345.67"
    - With sign: "-12345.67"
    - Empty: "" -> None
    - Tally internal format: "12345.67 Dr" / "12345.67 Cr" (signed by suffix)
    """
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None
    if raw == "0":
        return Decimal(0)

    # Handle Tally Dr/Cr suffixes
    multiplier = 1
    upper = raw.upper()
    if upper.endswith(" CR"):
        raw = raw[:-3].strip()
        multiplier = 1   # Cr is positive in accounting
    elif upper.endswith(" DR"):
        raw = raw[:-3].strip()
        multiplier = -1  # Dr is negative in accounting
    elif upper.endswith("CR"):
        raw = raw[:-2].strip()
        multiplier = 1
    elif upper.endswith("DR"):
        raw = raw[:-2].strip()
        multiplier = -1

    # Remove commas (e.g. "1,234.56")
    raw = raw.replace(",", "")

    try:
        amount = Decimal(raw) * multiplier
        return amount
    except InvalidOperation:
        logger.warning(
            "Cannot parse amount in '%s.%s': %r (record=%s) - using NULL",
            collection, field, value, record_id,
        )
        return None


def transform_quantity(value: Any, collection: str = "", field: str = "",
                       record_id: Optional[str] = None) -> Optional[Decimal]:
    """Transform a Tally quantity (may include UOM suffix, e.g. '5 KG')."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Remove UOM suffix (letters after the number)
    num_part = re.split(r"[A-Za-z]", raw)[0].strip().replace(",", "")
    if not num_part:
        return None
    try:
        return Decimal(num_part)
    except InvalidOperation:
        logger.warning("Cannot parse quantity '%s.%s': %r", collection, field, value)
        return None


def transform_boolean(
    value: Any,
    collection: str = "",
    field: str = "",
    record_id: Optional[str] = None,
) -> Optional[bool]:
    """Transform a Tally boolean to Python bool.

    SQL Server BIT column accepts True/False/None.
    """
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in TALLY_TRUE_VALUES:
        return True
    if raw in TALLY_FALSE_VALUES:
        return False
    logger.debug("Unknown boolean value in '%s.%s': %r -> None", collection, field, value)
    return None


def transform_integer(
    value: Any,
    collection: str = "",
    field: str = "",
    record_id: Optional[str] = None,
) -> Optional[int]:
    """Transform a Tally integer field."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return int(float(raw))  # handle '123.0' -> 123
    except (ValueError, OverflowError):
        logger.warning("Cannot parse integer '%s.%s': %r", collection, field, value)
        return None


def transform_guid(
    value: Any,
    collection: str = "",
    field: str = "",
    record_id: Optional[str] = None,
) -> Optional[str]:
    """Normalise a Tally GUID.

    Tally GUIDs are typically 'CompanyID-GUID' format.
    Stored as NVARCHAR(50) - no conversion to UUID type.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", ""):
        return None
    # Truncate if absurdly long (protect DB column)
    if len(s) > 64:
        logger.warning("GUID truncated in '%s.%s': %r", collection, field, s)
        s = s[:64]
    return s


def transform_large_text(
    value: Any,
    collection: str = "",
    field: str = "",
    record_id: Optional[str] = None,
) -> Optional[str]:
    """Transform large text (NVARCHAR(MAX) columns like narration)."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


# ---------------------------------------------------------------------------
# Record-level transformation
# ---------------------------------------------------------------------------

# Field type -> transform function map
_TRANSFORM_MAP = {
    "guid": transform_guid,
    "text": transform_text,
    "date": transform_date,
    "datetime": transform_date,
    "amount": transform_amount,
    "quantity": transform_quantity,
    "rate": transform_amount,
    "logical": transform_boolean,
    "integer": transform_integer,
    "large_text": transform_large_text,
    "unknown": transform_text,
}


def transform_record(
    raw_record: dict[str, Any],
    field_type_map: dict[str, str],  # {field_name: tally_type}
    collection: str = "",
    record_id: Optional[str] = None,
) -> dict[str, Any]:
    """Apply per-field transforms to a raw Tally record.

    Args:
        raw_record: Raw dict from Tally client.
        field_type_map: Maps field names to their Tally type strings.
        collection: Collection name (for error context).
        record_id: Source identifier (for error context).

    Returns:
        Transformed dict ready for SQL Server insertion.
    """
    out: dict[str, Any] = {}
    for field_name, raw_value in raw_record.items():
        field_type = field_type_map.get(field_name, "text")
        transform_fn = _TRANSFORM_MAP.get(field_type, transform_text)
        try:
            out[field_name.lower()] = transform_fn(
                raw_value,
                collection=collection,
                field=field_name,
                record_id=record_id,
            )
        except DataTransformError:
            raise
        except Exception as exc:
            logger.warning(
                "Unexpected transform error '%s.%s'=%r: %s",
                collection, field_name, raw_value, exc
            )
            out[field_name.lower()] = None
    return out
