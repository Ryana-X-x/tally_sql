"""Unit tests for data transformation functions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tally_migrator.migration.transforms import (
    transform_amount,
    transform_boolean,
    transform_date,
    transform_guid,
    transform_record,
    transform_text,
)


class TestTransformText:
    def test_plain_string(self):
        assert transform_text("hello") == "hello"

    def test_strips_whitespace(self):
        assert transform_text("  hello  ") == "hello"

    def test_none_returns_none(self):
        assert transform_text(None) is None

    def test_empty_string_returns_none(self):
        assert transform_text("") is None

    def test_max_length_truncation(self):
        result = transform_text("a" * 300, max_length=255)
        assert result is not None
        assert len(result) == 255

    def test_numeric_coerced(self):
        assert transform_text(123) == "123"


class TestTransformDate:
    def test_yyyymmdd_format(self):
        assert transform_date("20231201") == date(2023, 12, 1)

    def test_iso_format(self):
        assert transform_date("2023-12-01") == date(2023, 12, 1)

    def test_ddmmyyyy_format(self):
        assert transform_date("01/12/2023") == date(2023, 12, 1)

    def test_tally_null_date(self):
        assert transform_date("19000101") is None

    def test_epoch_date(self):
        assert transform_date("19000101") is None

    def test_zero_date(self):
        assert transform_date("00000000") is None

    def test_empty_string(self):
        assert transform_date("") is None

    def test_none(self):
        assert transform_date(None) is None

    def test_invalid_format_returns_none(self):
        result = transform_date("not-a-date", strict=False)
        assert result is None

    def test_invalid_format_strict_raises(self):
        from tally_migrator.exceptions import DataTransformError
        with pytest.raises(DataTransformError):
            transform_date("not-a-date", strict=True)


class TestTransformAmount:
    def test_plain_decimal(self):
        assert transform_amount("12345.67") == Decimal("12345.67")

    def test_negative_amount(self):
        assert transform_amount("-500.00") == Decimal("-500.00")

    def test_with_cr_suffix(self):
        result = transform_amount("1000.00 Cr")
        assert result == Decimal("1000.00")

    def test_with_dr_suffix(self):
        result = transform_amount("1000.00 Dr")
        assert result == Decimal("-1000.00")

    def test_none_returns_none(self):
        assert transform_amount(None) is None

    def test_empty_returns_zero(self):
        # Empty string for amount -> None
        assert transform_amount("") is None

    def test_with_comma(self):
        assert transform_amount("1,234.56") == Decimal("1234.56")

    def test_zero(self):
        assert transform_amount("0") == Decimal(0)


class TestTransformBoolean:
    def test_yes_is_true(self):
        assert transform_boolean("Yes") is True

    def test_no_is_false(self):
        assert transform_boolean("No") is False

    def test_true_string(self):
        assert transform_boolean("true") is True

    def test_false_string(self):
        assert transform_boolean("false") is False

    def test_none(self):
        assert transform_boolean(None) is None

    def test_empty_is_false(self):
        assert transform_boolean("") is False

    def test_one_is_true(self):
        assert transform_boolean("1") is True

    def test_zero_is_false(self):
        assert transform_boolean("0") is False


class TestTransformGuid:
    def test_valid_guid(self):
        guid = "10000000-0000-0000-0000-000000000001-00000000003A2F3D"
        assert transform_guid(guid) == guid

    def test_none_returns_none(self):
        assert transform_guid(None) is None

    def test_empty_returns_none(self):
        assert transform_guid("") is None

    def test_long_guid_truncated(self):
        long_guid = "x" * 100
        result = transform_guid(long_guid)
        assert result is not None
        assert len(result) <= 64


class TestTransformRecord:
    def test_basic_record(self):
        raw = {"GUID": "abc-123", "NAME": "Test", "ALTERID": "500"}
        field_map = {"GUID": "guid", "NAME": "text", "ALTERID": "integer"}
        result = transform_record(raw, field_map, collection="Ledger")
        assert result["guid"] == "abc-123"
        assert result["name"] == "Test"
        assert result["alterid"] == 500

    def test_date_field(self):
        raw = {"DATE": "20231201"}
        field_map = {"DATE": "date"}
        result = transform_record(raw, field_map)
        from datetime import date
        assert result["date"] == date(2023, 12, 1)

    def test_null_handling(self):
        raw = {"NAME": None, "GUID": "abc"}
        field_map = {"NAME": "text", "GUID": "guid"}
        result = transform_record(raw, field_map)
        assert result["name"] is None
        assert result["guid"] == "abc"
