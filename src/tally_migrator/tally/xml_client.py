"""TallyPrime XML/HTTP client implementation.

Communicates with TallyPrime via its built-in HTTP server using TDL XML requests.
This is the primary production integration mechanism.

TallyPrime exposes an HTTP server on port 9000 (configurable) that accepts
XML requests in a specific TDL format and returns XML responses.

Reference:
    TallyPrime Developer Reference
    https://developers.tally.co.in/
"""

from __future__ import annotations

import logging
from typing import Any, Generator, Optional
from xml.etree import ElementTree as ET

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    requests = None  # type: ignore

from tally_migrator.exceptions import (
    TallyConnectionError,
    TallyQueryError,
    TallyTimeoutError,
)
from tally_migrator.tally.base_client import BaseTallyClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known Tally collections and their default fields
# This is reference information based on standard TallyPrime TDL documentation.
# The schema discovery process will extend/override this at runtime.
# ---------------------------------------------------------------------------

KNOWN_COLLECTIONS: dict[str, dict] = {
    "Ledger": {
        "category": "master",
        "fields": [
            "GUID", "NAME", "PARENT", "OPENINGBALANCE", "CLOSINGBALANCE",
            "ISREVENUE", "DESCRIPTION", "CURRENCYNAME", "TAXTYPE",
            "GSTTYPE", "ALTERID", "MAILINGNAME",
        ],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "Group": {
        "category": "master",
        "fields": [
            "GUID", "NAME", "PARENT", "ISSUBLEDGER", "ISDEEMEDPOSITIVE",
            "AFFECTSGROSSPROFIT", "ALTERID",
        ],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "StockItem": {
        "category": "master",
        "fields": [
            "GUID", "NAME", "PARENT", "CATEGORY", "BASEUNITS",
            "OPENINGBALANCE", "OPENINGRATE", "OPENINGVALUE",
            "GSTAPPLICABLE", "ALTERID", "DESCRIPTION",
        ],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "StockGroup": {
        "category": "master",
        "fields": ["GUID", "NAME", "PARENT", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "VoucherType": {
        "category": "master",
        "fields": ["GUID", "NAME", "PARENT", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "Currency": {
        "category": "master",
        "fields": ["GUID", "NAME", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "CostCentre": {
        "category": "master",
        "fields": ["GUID", "NAME", "PARENT", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "CostCategory": {
        "category": "master",
        "fields": ["GUID", "NAME", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "Godown": {
        "category": "master",
        "fields": ["GUID", "NAME", "PARENT", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "Unit": {
        "category": "master",
        "fields": ["GUID", "NAME", "ALTERID"],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
    },
    "Voucher": {
        "category": "transaction",
        "fields": [
            "GUID", "VOUCHERTYPENAME", "VOUCHERNUMBER", "DATE",
            "EFFECTIVEDATE", "PARTYLEDGERNAME", "NARRATION",
            "REFERENCE", "ISCANCELLED", "ISOPTIONAL",
            "VOUCHERTYPEGUID", "ALTERID",
        ],
        "key_field": "GUID",
        "alteration_field": "ALTERID",
        "child_collections": [
            "AllLedgerEntries", "AllInventoryEntries",
            "BillAllocations", "BankAllocations", "BatchAllocations",
        ],
    },
}

# ---------------------------------------------------------------------------
# TDL XML templates
# ---------------------------------------------------------------------------

COLLECTION_REQUEST_TEMPLATE = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>{collection_name}</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        {company_clause}
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="{collection_name}" ISMODIFY="No">
            <TYPE>{collection_name}</TYPE>
            {field_clauses}
            {filter_clause}
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""

COMPANY_CLAUSE = "<SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>"
FETCH_TEMPLATE = "<FETCH>{field}</FETCH>"
FETCH_ALL_TEMPLATE = ""

COMPANY_INFO_REQUEST = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Companies</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="List of Companies" ISMODIFY="No">
            <TYPE>Company</TYPE>
            <FETCH>NAME</FETCH>
            <FETCH>GUID</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
  </BODY>
</ENVELOPE>"""


def _build_session(timeout: int, max_retries: int = 2) -> "requests.Session":
    """Build a requests Session with retry support."""
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    return session


class XMLTallyClient(BaseTallyClient):
    """Tally HTTP/XML client.

    Communicates with TallyPrime's built-in HTTP server using TDL XML requests.

    Connection lifecycle:
        client = XMLTallyClient(config)
        client.connect()      # verifies reachability
        for record in client.fetch_all('Ledger'):
            ...
        client.close()

    Or use as context manager:
        with XMLTallyClient(config) as client:
            ...
    """

    def __init__(self, config):
        super().__init__(config)
        self._session: Optional["requests.Session"] = None
        self._connected = False

    def connect(self) -> None:
        if requests is None:
            raise TallyConnectionError(
                self.config.host, self.config.port,
                "'requests' library not installed. Run: pip install requests"
            )
        logger.info("Connecting to Tally XML server at %s:%s", self.config.host, self.config.port)
        self._session = _build_session(self.config.timeout_seconds)
        # Probe with a lightweight request
        try:
            info = self.test_connection()
            logger.info("Tally XML connection established. Info: %s", info)
            self._connected = True
        except TallyConnectionError:
            raise
        except Exception as exc:
            raise TallyConnectionError(self.config.host, self.config.port, str(exc)) from exc

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None
        self._connected = False

    def test_connection(self) -> dict[str, Any]:
        xml = COMPANY_INFO_REQUEST
        try:
            resp = self._post(xml)
            companies = self._parse_collection(resp, "COMPANY")
            return {
                "status": "ok",
                "company_count": len(companies),
                "host": self.config.host,
                "port": self.config.port,
            }
        except TallyConnectionError:
            raise
        except Exception as exc:
            raise TallyConnectionError(self.config.host, self.config.port, str(exc)) from exc

    def list_companies(self) -> list[str]:
        resp = self._post(COMPANY_INFO_REQUEST)
        companies = self._parse_collection(resp, "COMPANY")
        return [c.get("NAME", "") for c in companies if c.get("NAME")]

    def get_company_info(self, company: Optional[str] = None) -> dict[str, Any]:
        resp = self._post(COMPANY_INFO_REQUEST)
        companies = self._parse_collection(resp, "COMPANY")
        if company:
            for c in companies:
                if c.get("NAME", "").upper() == company.upper():
                    return c
        return companies[0] if companies else {}

    def list_collections(self) -> list[str]:
        """Return known collection names (extended by discovery)."""
        return list(KNOWN_COLLECTIONS.keys())

    def describe_collection(self, collection_name: str) -> dict[str, Any]:
        known = KNOWN_COLLECTIONS.get(collection_name, {})
        # Try to fetch a single record to determine actual fields
        fields = known.get("fields", [])
        return {
            "name": collection_name,
            "category": known.get("category", "unknown"),
            "fields": [{"name": f, "type": "text"} for f in fields],
            "key_field": known.get("key_field"),
            "alteration_field": known.get("alteration_field"),
            "child_collections": known.get("child_collections", []),
        }

    def count_records(
        self,
        collection_name: str,
        company: Optional[str] = None,
    ) -> int:
        """Fetch full collection and count (no count-only API available in standard TDL)."""
        count = 0
        try:
            for _ in self.fetch_all(collection_name, fields=["GUID"], company=company):
                count += 1
        except Exception:
            pass
        return count

    def fetch_all(
        self,
        collection_name: str,
        fields: Optional[list[str]] = None,
        filters: Optional[dict[str, Any]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield all records from a Tally collection."""
        xml = self._build_collection_request(collection_name, fields=fields, company=company)
        resp = self._post(xml)
        tag = collection_name.upper()
        for record in self._parse_collection(resp, tag):
            yield record

    def fetch_since(
        self,
        collection_name: str,
        since_marker: Optional[str],
        fields: Optional[list[str]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield records altered since since_marker.

        TallyPrime tracks alterations via ALTERID (an incrementing integer).
        We filter by ALTERID > last_known_alterid for masters.
        For vouchers we filter by DATE or ALTERID depending on availability.

        If since_marker is None, yields all records.
        """
        known = KNOWN_COLLECTIONS.get(collection_name, {})
        alteration_field = known.get("alteration_field", "ALTERID")

        if since_marker is None or since_marker == "":
            yield from self.fetch_all(collection_name, fields=fields, company=company)
            return

        # Build a filter expression for ALTERID-based filtering
        # TDL filter: $$Number:$ALTERID > 12345
        filter_expr = None
        try:
            marker_int = int(since_marker)
            filter_expr = f"$$Number:${alteration_field} > {marker_int}"
        except (ValueError, TypeError):
            # Fallback: date-based filter
            filter_expr = f"$DATE >= {since_marker}"

        xml = self._build_collection_request(
            collection_name, fields=fields, company=company,
            filter_expr=filter_expr
        )
        resp = self._post(xml)
        tag = collection_name.upper()
        for record in self._parse_collection(resp, tag):
            yield record

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, xml: str) -> str:
        """POST XML to Tally HTTP server and return response text."""
        if self._session is None:
            # Auto-connect for test_connection calls before _connected is set
            self._session = _build_session(self.config.timeout_seconds)

        url = self.config.xml_base_url
        try:
            resp = self._session.post(
                url,
                data=xml.encode(self.config.xml_encoding, errors="replace"),
                headers={"Content-Type": "application/xml"},
                timeout=self.config.timeout_seconds,
            )
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.Timeout:
            raise TallyTimeoutError(
                self.config.host, self.config.port,
                f"Request timed out after {self.config.timeout_seconds}s"
            )
        except requests.exceptions.ConnectionError as exc:
            raise TallyConnectionError(
                self.config.host, self.config.port, str(exc)
            ) from exc
        except requests.exceptions.HTTPError as exc:
            raise TallyQueryError("<unknown>", str(exc)) from exc

    def _build_collection_request(
        self,
        collection_name: str,
        fields: Optional[list[str]] = None,
        company: Optional[str] = None,
        filter_expr: Optional[str] = None,
    ) -> str:
        """Build a TDL collection export XML request."""
        company_name = company or self.config.company_name
        company_clause = COMPANY_CLAUSE.format(company=company_name) if company_name else ""

        if fields:
            field_clauses = "\n            ".join(
                FETCH_TEMPLATE.format(field=f) for f in fields
            )
        else:
            # Fetch all known fields for this collection
            known_fields = KNOWN_COLLECTIONS.get(collection_name, {}).get("fields", [])
            if known_fields:
                field_clauses = "\n            ".join(
                    FETCH_TEMPLATE.format(field=f) for f in known_fields
                )
            else:
                field_clauses = ""

        filter_clause = ""
        if filter_expr:
            safe_filter = filter_expr.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            filter_clause = f"<FILTER>{safe_filter}</FILTER>"
            # Note: Full TDL filter syntax is complex; this is a simplified version
            # Real implementation would use proper TDL filter syntax

        return COLLECTION_REQUEST_TEMPLATE.format(
            collection_name=collection_name,
            company_clause=company_clause,
            field_clauses=field_clauses,
            filter_clause=filter_clause,
        )

    def _parse_collection(self, xml_text: str, tag: str) -> list[dict[str, Any]]:
        """Parse XML response and extract records as dicts."""
        records = []
        try:
            # Handle potential encoding declaration issues
            if xml_text.startswith("\ufeff"):
                xml_text = xml_text[1:]

            root = ET.fromstring(xml_text.encode("utf-8"))
        except ET.ParseError as exc:
            logger.error("XML parse error: %s", exc)
            logger.debug("Raw XML (first 500 chars): %s", xml_text[:500])
            return records

        for elem in root.iter(tag):
            record: dict[str, Any] = {}
            for child in elem:
                text = (child.text or "").strip()
                record[child.tag] = text
            # Also handle attributes
            for attr_name, attr_val in elem.attrib.items():
                record[f"@{attr_name}"] = attr_val
            records.append(record)

        return records

    def _parse_collection_streaming(
        self, xml_text: str, tag: str
    ) -> Generator[dict[str, Any], None, None]:
        """Parse XML iteratively for large responses (reduces memory)."""
        try:
            if xml_text.startswith("\ufeff"):
                xml_text = xml_text[1:]
            import io
            stream = io.StringIO(xml_text)
            for event, elem in ET.iterparse(stream, events=("end",)):
                if elem.tag == tag:
                    record: dict[str, Any] = {}
                    for child in elem:
                        record[child.tag] = (child.text or "").strip()
                    for attr_name, attr_val in elem.attrib.items():
                        record[f"@{attr_name}"] = attr_val
                    yield record
                    elem.clear()  # free memory
        except ET.ParseError as exc:
            logger.error("XML streaming parse error: %s", exc)
