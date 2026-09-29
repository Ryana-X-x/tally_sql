"""Realistic Tally data fixtures for testing.

These represent the structure of records returned by the Tally client.
They mirror what XMLTallyClient.fetch_all() would yield.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Ledger fixtures
# ---------------------------------------------------------------------------

SAMPLE_LEDGERS: list[dict[str, Any]] = [
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000003A2F3D",
        "NAME": "Sundry Debtors",
        "PARENT": "Sundry Debtors",
        "OPENINGBALANCE": "150000.00",
        "CLOSINGBALANCE": "250000.00",
        "ISREVENUE": "No",
        "DESCRIPTION": "",
        "CURRENCYNAME": "INR",
        "ALTERID": "1500",
        "TAXTYPE": "",
        "GSTTYPE": "",
        "MAILINGNAME": "Sundry Debtors",
    },
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000003A2F3E",
        "NAME": "ABC Industries",
        "PARENT": "Sundry Debtors",
        "OPENINGBALANCE": "75000.00",
        "CLOSINGBALANCE": "125000.00",
        "ISREVENUE": "No",
        "DESCRIPTION": "Key customer account",
        "CURRENCYNAME": "INR",
        "ALTERID": "1501",
        "TAXTYPE": "GST",
        "GSTTYPE": "Regular",
        "MAILINGNAME": "ABC Industries Pvt Ltd",
    },
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000003A2F3F",
        "NAME": "Sales Account",
        "PARENT": "Sales Accounts",
        "OPENINGBALANCE": "0.00",
        "CLOSINGBALANCE": "-500000.00",
        "ISREVENUE": "Yes",
        "DESCRIPTION": "",
        "CURRENCYNAME": "INR",
        "ALTERID": "1502",
        "TAXTYPE": "",
        "GSTTYPE": "",
        "MAILINGNAME": "",
    },
]

# ---------------------------------------------------------------------------
# Group fixtures
# ---------------------------------------------------------------------------

SAMPLE_GROUPS: list[dict[str, Any]] = [
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000002A1B2C",
        "NAME": "Sundry Debtors",
        "PARENT": "Current Assets",
        "ISSUBLEDGER": "Yes",
        "ISDEEMEDPOSITIVE": "Yes",
        "AFFECTSGROSSPROFIT": "No",
        "ALTERID": "100",
    },
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000002A1B2D",
        "NAME": "Sales Accounts",
        "PARENT": "Income",
        "ISSUBLEDGER": "No",
        "ISDEEMEDPOSITIVE": "No",
        "AFFECTSGROSSPROFIT": "Yes",
        "ALTERID": "101",
    },
]

# ---------------------------------------------------------------------------
# StockItem fixtures
# ---------------------------------------------------------------------------

SAMPLE_STOCK_ITEMS: list[dict[str, Any]] = [
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000005C3D4E",
        "NAME": "Widget A",
        "PARENT": "Finished Goods",
        "CATEGORY": "",
        "BASEUNITS": "Nos",
        "OPENINGBALANCE": "100",
        "OPENINGRATE": "250.00 /Nos",
        "OPENINGVALUE": "25000.00",
        "GSTAPPLICABLE": "Applicable",
        "ALTERID": "2000",
        "DESCRIPTION": "Standard widget",
    },
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000005C3D4F",
        "NAME": "Raw Material B",
        "PARENT": "Raw Materials",
        "CATEGORY": "",
        "BASEUNITS": "KG",
        "OPENINGBALANCE": "500",
        "OPENINGRATE": "50.00 /KG",
        "OPENINGVALUE": "25000.00",
        "GSTAPPLICABLE": "Applicable",
        "ALTERID": "2001",
        "DESCRIPTION": "",
    },
]

# ---------------------------------------------------------------------------
# Voucher fixtures
# ---------------------------------------------------------------------------

SAMPLE_VOUCHERS: list[dict[str, Any]] = [
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "VOUCHERTYPENAME": "Sales",
        "VOUCHERNUMBER": "Sal-001",
        "DATE": "20231201",
        "EFFECTIVEDATE": "20231201",
        "PARTYLEDGERNAME": "ABC Industries",
        "NARRATION": "Sale of Widget A",
        "REFERENCE": "PO-2023-001",
        "ISCANCELLED": "No",
        "ISOPTIONAL": "No",
        "ALTERID": "5000",
    },
    {
        "GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6H",
        "VOUCHERTYPENAME": "Purchase",
        "VOUCHERNUMBER": "Pur-001",
        "DATE": "20231202",
        "EFFECTIVEDATE": "20231202",
        "PARTYLEDGERNAME": "XYZ Suppliers",
        "NARRATION": "Purchase of Raw Material B",
        "REFERENCE": "",
        "ISCANCELLED": "No",
        "ISOPTIONAL": "No",
        "ALTERID": "5001",
    },
]

# ---------------------------------------------------------------------------
# Voucher sub-entry fixtures
# ---------------------------------------------------------------------------

SAMPLE_LEDGER_ENTRIES: list[dict[str, Any]] = [
    {
        "VOUCHER_GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "LEDGER_NAME": "ABC Industries",
        "AMOUNT": "59000.00",
        "IS_PARTY": True,
        "ENTRY_TYPE": "dr",
    },
    {
        "VOUCHER_GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "LEDGER_NAME": "Sales Account",
        "AMOUNT": "-50000.00",
        "IS_PARTY": False,
        "ENTRY_TYPE": "cr",
    },
    {
        "VOUCHER_GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "LEDGER_NAME": "CGST @ 9%",
        "AMOUNT": "-4500.00",
        "IS_PARTY": False,
        "ENTRY_TYPE": "cr",
    },
    {
        "VOUCHER_GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "LEDGER_NAME": "SGST @ 9%",
        "AMOUNT": "-4500.00",
        "IS_PARTY": False,
        "ENTRY_TYPE": "cr",
    },
]

SAMPLE_INVENTORY_ENTRIES: list[dict[str, Any]] = [
    {
        "VOUCHER_GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "STOCK_ITEM_NAME": "Widget A",
        "QUANTITY": "10 Nos",
        "RATE": "5000.00 /Nos",
        "AMOUNT": "-50000.00",
        "UOM": "Nos",
        "GODOWN_NAME": "Main Godown",
    },
]

SAMPLE_BILL_ALLOCATIONS: list[dict[str, Any]] = [
    {
        "VOUCHER_GUID": "10000000-0000-0000-0000-000000000001-00000000007E5F6G",
        "BILL_NAME": "Sal-001",
        "BILL_TYPE": "New Ref",
        "AMOUNT": "59000.00",
        "DUE_DATE": "20240101",
    },
]

# ---------------------------------------------------------------------------
# Schema fixture (mock discovered_schema.json content)
# ---------------------------------------------------------------------------

SAMPLE_DISCOVERED_SCHEMA = {
    "version": "1.0",
    "discovery_timestamp": "2024-01-01T00:00:00+00:00",
    "tally_host": "localhost",
    "tally_company": "Test Company",
    "collections": {
        "Ledger": {
            "name": "Ledger",
            "category": "master",
            "fields": {
                "GUID": {
                    "name": "GUID",
                    "tally_type": "guid",
                    "sql_type": "NVARCHAR(50)",
                    "nullable": False,
                    "is_primary_key": True,
                    "is_foreign_key": False,
                    "references_collection": None,
                    "references_field": None,
                    "is_change_marker": False,
                    "sample_values": [],
                    "description": "Globally unique identifier assigned by Tally",
                    "is_custom_tdl": False,
                    "mapped": True,
                },
                "NAME": {
                    "name": "NAME",
                    "tally_type": "text",
                    "sql_type": "NVARCHAR(255)",
                    "nullable": True,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "references_collection": None,
                    "references_field": None,
                    "is_change_marker": False,
                    "sample_values": [],
                    "description": "Primary display name",
                    "is_custom_tdl": False,
                    "mapped": True,
                },
                "ALTERID": {
                    "name": "ALTERID",
                    "tally_type": "integer",
                    "sql_type": "BIGINT",
                    "nullable": True,
                    "is_primary_key": False,
                    "is_foreign_key": False,
                    "references_collection": None,
                    "references_field": None,
                    "is_change_marker": True,
                    "sample_values": [],
                    "description": "Alteration ID - increments on each modification (used for incremental sync)",
                    "is_custom_tdl": False,
                    "mapped": True,
                },
            },
            "child_collections": [],
            "parent_collection": None,
            "sql_table_name": "ledger",
            "primary_key_field": "GUID",
            "change_marker_field": "ALTERID",
            "estimated_row_count": 0,
            "unmapped_fields": [],
            "description": "Tally Ledger collection (master)",
        }
    },
}
