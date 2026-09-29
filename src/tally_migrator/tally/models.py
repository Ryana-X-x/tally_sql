"""Data models for Tally objects used in migration.

These models are used to carry data between the Tally client layer
and the migration pipeline. They are NOT the final SQL schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional


@dataclass
class TallyField:
    """Metadata about a single field in a Tally collection."""
    name: str
    tally_type: str          # 'text', 'date', 'amount', 'logical', 'number'
    sample_values: list[Any] = field(default_factory=list)
    nullable: bool = True
    is_guid: bool = False
    is_key: bool = False
    description: str = ""


@dataclass
class TallyCollection:
    """Metadata about a Tally collection discovered at runtime."""
    name: str
    category: str = "unknown"   # 'master', 'transaction', 'config'
    fields: dict[str, TallyField] = field(default_factory=dict)
    child_collections: list[str] = field(default_factory=list)
    parent_collection: Optional[str] = None
    estimated_row_count: int = 0
    primary_key_field: Optional[str] = None
    alteration_field: Optional[str] = None    # field tracking changes
    source_description: str = ""


@dataclass
class TallyRecord:
    """A single raw record from a Tally collection."""
    collection: str
    data: dict[str, Any]
    source_id: Optional[str] = None   # stable identifier (GUID, name, etc.)
    child_records: dict[str, list["TallyRecord"]] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class TallyLedger:
    """Structured model for a Tally Ledger master."""
    guid: Optional[str]
    name: str
    alias_name: Optional[str]
    parent: Optional[str]           # Group name
    opening_balance: Optional[Decimal]
    closing_balance: Optional[Decimal]
    is_revenue: Optional[bool]
    description: Optional[str]
    currency_name: Optional[str]
    tax_type: Optional[str]
    gst_type: Optional[str]
    pan_number: Optional[str]
    registration_type: Optional[str]
    alteration_id: Optional[str]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyGroup:
    """Structured model for a Tally Group master."""
    guid: Optional[str]
    name: str
    parent: Optional[str]
    is_subledger: Optional[bool]
    is_deemed_positive: Optional[bool]
    affects_gross_profit: Optional[bool]
    alteration_id: Optional[str]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyStockItem:
    """Structured model for a Tally StockItem master."""
    guid: Optional[str]
    name: str
    alias_name: Optional[str]
    parent: Optional[str]           # StockGroup name
    category: Optional[str]
    uom: Optional[str]
    opening_balance: Optional[Decimal]
    opening_rate: Optional[Decimal]
    opening_value: Optional[Decimal]
    gstin: Optional[str]
    hsn_code: Optional[str]
    alteration_id: Optional[str]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyVoucher:
    """Structured model for a TallyPrime Voucher."""
    guid: Optional[str]
    voucher_type: str
    voucher_number: Optional[str]
    date: Optional[date]
    party_name: Optional[str]
    narration: Optional[str]
    reference: Optional[str]
    is_cancelled: bool = False
    is_optional: bool = False
    company_name: Optional[str] = None
    currency_name: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    alteration_id: Optional[str] = None
    effective_date: Optional[date] = None
    ledger_entries: list["TallyVoucherLedgerEntry"] = field(default_factory=list)
    inventory_entries: list["TallyVoucherInventoryEntry"] = field(default_factory=list)
    bill_allocations: list["TallyVoucherBillAllocation"] = field(default_factory=list)
    bank_entries: list["TallyVoucherBankEntry"] = field(default_factory=list)
    batch_allocations: list["TallyVoucherBatchAllocation"] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyVoucherLedgerEntry:
    voucher_guid: Optional[str]
    ledger_name: str
    amount: Optional[Decimal]
    is_party: bool = False
    currency_name: Optional[str] = None
    forex_amount: Optional[Decimal] = None
    entry_type: Optional[str] = None     # 'dr' or 'cr'
    gst_class: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyVoucherInventoryEntry:
    voucher_guid: Optional[str]
    stock_item_name: str
    quantity: Optional[Decimal]
    rate: Optional[Decimal]
    amount: Optional[Decimal]
    uom: Optional[str]
    godown_name: Optional[str] = None
    batch_name: Optional[str] = None
    tracking_number: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyVoucherBillAllocation:
    voucher_guid: Optional[str]
    bill_name: str
    bill_type: Optional[str]
    amount: Optional[Decimal]
    due_date: Optional[date] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyVoucherBankEntry:
    voucher_guid: Optional[str]
    instrument_date: Optional[date]
    instrument_number: Optional[str]
    bank_name: Optional[str]
    amount: Optional[Decimal]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TallyVoucherBatchAllocation:
    voucher_guid: Optional[str]
    batch_name: Optional[str]
    destination_godown: Optional[str]
    quantity: Optional[Decimal]
    amount: Optional[Decimal]
    manufactured_on: Optional[date] = None
    expiry_period: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)
