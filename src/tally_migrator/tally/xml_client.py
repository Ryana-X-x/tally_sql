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

import codecs
import logging
import re
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
        fields = list(known.get("fields", []))

        # Attempt dynamic discovery if connected
        if self._connected:
            try:
                # Fetch a sample record to discover fields
                for record in self.fetch_all(collection_name, fields=None):
                    for k in record.keys():
                        if not k.startswith("@") and not k.startswith("_") and k not in fields:
                            fields.append(k)
                    break  # sample first record
            except Exception as exc:
                logger.debug("Dynamic field sampling failed for %s: %s", collection_name, exc)

        return {
            "name": collection_name,
            "category": known.get("category", "unknown"),
            "fields": [{"name": f, "type": "text"} for f in fields],
            "key_field": known.get("key_field", "GUID"),
            "alteration_field": known.get("alteration_field", "ALTERID"),
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
        """Yield all records from a Tally collection using streaming parser."""
        xml = self._build_collection_request(collection_name, fields=fields, company=company)
        resp = self._post_stream(xml)
        tag = collection_name.upper()
        try:
            yield from self._parse_collection_streaming(resp, tag)
        finally:
            if hasattr(resp, "close"):
                resp.close()

    def fetch_since(
        self,
        collection_name: str,
        since_marker: Optional[str],
        fields: Optional[list[str]] = None,
        company: Optional[str] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield records altered since since_marker using streaming parser."""
        known = KNOWN_COLLECTIONS.get(collection_name, {})
        alteration_field = known.get("alteration_field", "ALTERID")

        if since_marker is None or since_marker == "":
            yield from self.fetch_all(collection_name, fields=fields, company=company)
            return

        filter_expr = None
        try:
            marker_int = int(since_marker)
            filter_expr = f"$$Number:${alteration_field} > {marker_int}"
        except (ValueError, TypeError):
            filter_expr = f"$DATE >= {since_marker}"

        xml = self._build_collection_request(
            collection_name, fields=fields, company=company,
            filter_expr=filter_expr
        )
        resp = self._post_stream(xml)
        tag = collection_name.upper()
        try:
            yield from self._parse_collection_streaming(resp, tag)
        finally:
            if hasattr(resp, "close"):
                resp.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, xml: str) -> str:
        """POST XML to Tally HTTP server and return response text."""
        resp = self._post_stream(xml)
        try:
            return resp.text
        finally:
            if hasattr(resp, "close"):
                resp.close()

    def _post_stream(self, xml: str) -> Any:
        """POST XML to Tally HTTP server and return streaming response object."""
        if self._session is None:
            self._session = _build_session(self.config.timeout_seconds)

        url = self.config.xml_base_url
        try:
            resp = self._session.post(
                url,
                data=xml.encode(self.config.xml_encoding, errors="replace"),
                headers={"Content-Type": "application/xml"},
                timeout=self.config.timeout_seconds,
                stream=True,
            )
            resp.raise_for_status()
            return resp
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

        return COLLECTION_REQUEST_TEMPLATE.format(
            collection_name=collection_name,
            company_clause=company_clause,
            field_clauses=field_clauses,
            filter_clause=filter_clause,
        )

    def _parse_collection(self, xml_text: str, tag: str) -> list[dict[str, Any]]:
        """Parse XML response and extract records as dicts."""
        return list(self._parse_collection_streaming(xml_text, tag))

    def _parse_collection_streaming(
        self, xml_source: Any, tag: str
    ) -> Generator[dict[str, Any], None, None]:
        """Parse XML iteratively using pyexpat streaming parser with pre-parse sanitization."""
        import pyexpat

        target_tag = tag.upper()
        parser = StreamingTallyXMLParser(target_tag)
        sanitizer = StreamingXMLSanitizer()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        try:
            if isinstance(xml_source, str):
                if xml_source.startswith("\ufeff"):
                    xml_source = xml_source[1:]
                chunks = [xml_source.encode("utf-8")]
            elif isinstance(xml_source, bytes):
                if xml_source.startswith(b"\xef\xbb\xbf"):
                    xml_source = xml_source[3:]
                chunks = [xml_source]
            elif hasattr(xml_source, "iter_content") and not hasattr(xml_source.iter_content, "return_value"):
                chunks = xml_source.iter_content(chunk_size=65536)
            elif hasattr(xml_source, "text") and isinstance(xml_source.text, str) and xml_source.text:
                chunks = [xml_source.text.encode("utf-8")]
            elif hasattr(xml_source, "raw") and hasattr(xml_source.raw, "read"):
                def _stream_raw_gen():
                    while True:
                        c = xml_source.raw.read(65536)
                        if not c:
                            break
                        yield c
                chunks = _stream_raw_gen()
            elif hasattr(xml_source, "read"):
                def _stream_read_gen():
                    while True:
                        c = xml_source.read(65536)
                        if not c:
                            break
                        yield c
                chunks = _stream_read_gen()
            else:
                chunks = [xml_source]

            first_chunk = True
            for chunk in chunks:
                if not chunk:
                    continue
                if isinstance(chunk, str):
                    chunk_text = chunk
                else:
                    chunk_text = decoder.decode(chunk, final=False)

                sanitized_text = sanitizer.feed(chunk_text, is_final=False)
                if not sanitized_text:
                    continue

                chunk_bytes = sanitized_text.encode("utf-8")
                if first_chunk:
                    if chunk_bytes.startswith(b"\xef\xbb\xbf"):
                        chunk_bytes = chunk_bytes[3:]
                    first_chunk = False

                elements = parser.feed(chunk_bytes)
                for elem in elements:
                    parsed = _parse_xml_element(elem)
                    if isinstance(parsed, dict):
                        yield parsed
                    elem.clear()

            # Flush tail
            tail_text = decoder.decode(b"", final=True)
            final_text = sanitizer.feed(tail_text, is_final=True) + sanitizer.flush()
            if final_text:
                chunk_bytes = final_text.encode("utf-8")
                if first_chunk:
                    if chunk_bytes.startswith(b"\xef\xbb\xbf"):
                        chunk_bytes = chunk_bytes[3:]
                    first_chunk = False

                elements = parser.feed(chunk_bytes)
                for elem in elements:
                    parsed = _parse_xml_element(elem)
                    if isinstance(parsed, dict):
                        yield parsed
                    elem.clear()

            if not first_chunk:
                final_elements = parser.close()
                for elem in final_elements:
                    parsed = _parse_xml_element(elem)
                    if isinstance(parsed, dict):
                        yield parsed
                    elem.clear()

            if sanitizer.sanitized_count > 0:
                code_points_str = ", ".join(sorted(sanitizer.sanitized_code_points))
                logger.info(
                    "Sanitized %d invalid XML character reference(s) for collection/tag '%s' (code points: %s)",
                    sanitizer.sanitized_count, tag, code_points_str
                )

        except pyexpat.ExpatError as exc:
            logger.error("XML streaming parse error for tag '%s': %s", tag, exc)
            raise TallyQueryError(tag, f"XML streaming parse error: {exc}") from exc
        except Exception as exc:
            logger.error("Streaming XML extraction error for tag '%s': %s", tag, exc)
            raise TallyQueryError(tag, f"XML streaming extraction error: {exc}") from exc


NUMERIC_ENTITY_REGEX = re.compile(r"&#([0-9]+);|&#[xX]([0-9a-fA-F]+);")
INCOMPLETE_NUMERIC_ENTITY_REGEX = re.compile(r"&(?:#[0-9]*|#[xX][0-9a-fA-F]*)?$")
RAW_CONTROL_CHAR_REGEX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def is_valid_xml_character(code_point: int) -> bool:
    """Check if code_point is a legal XML 1.0 character according to W3C specification."""
    if code_point in (0x9, 0xA, 0xD):
        return True
    if 0x20 <= code_point <= 0xD7FF:
        return True
    if 0xE000 <= code_point <= 0xFFFD:
        return True
    if 0x10000 <= code_point <= 0x10FFFF:
        return True
    return False


class StreamingXMLSanitizer:
    """Stateful streaming sanitizer for malformed XML character references and control bytes.

    Handles incomplete entity references split across HTTP stream chunk boundaries
    (e.g., chunk 1: "... &#x", chunk 2: "1F; ..."). Sanitizes illegal XML 1.0 character
    references (such as &#0;, &#x0;, &#x1F;, &#31;) and raw control bytes before XML parsing.
    """

    def __init__(self):
        self.buffer = ""
        self.sanitized_count = 0
        self.sanitized_code_points: set[str] = set()

    def feed(self, chunk_text: str, is_final: bool = False) -> str:
        data = self.buffer + chunk_text
        self.buffer = ""

        if not is_final:
            m = INCOMPLETE_NUMERIC_ENTITY_REGEX.search(data)
            if m:
                idx = m.start()
                self.buffer = data[idx:]
                data = data[:idx]

        def _replace_entity(match: re.Match) -> str:
            dec_str = match.group(1)
            hex_str = match.group(2)
            cp = int(dec_str) if dec_str is not None else int(hex_str, 16)

            if is_valid_xml_character(cp):
                return match.group(0)
            else:
                self.sanitized_count += 1
                self.sanitized_code_points.add(f"U+{cp:04X}")
                return ""

        def _replace_raw_control(match: re.Match) -> str:
            cp = ord(match.group(0))
            self.sanitized_count += 1
            self.sanitized_code_points.add(f"U+{cp:04X}")
            return ""

        res = RAW_CONTROL_CHAR_REGEX.sub(_replace_raw_control, data)
        res = NUMERIC_ENTITY_REGEX.sub(_replace_entity, res)
        return res

    def flush(self) -> str:
        return self.feed("", is_final=True)


class StreamingTallyXMLParser:
    """Incremental streaming XML parser using pyexpat without namespace enforcement.

    Parses Tally XML responses containing arbitrary or undeclared namespace prefixes
    (e.g., <UDF:FIELD_NAME>, <sys:PARAM>) without raising unbound prefix errors.
    Yields ET.Element objects for matching target_tag in bounded memory.
    """

    def __init__(self, target_tag: str):
        import pyexpat
        self.target_tag = target_tag.upper()
        # Create pyexpat parser WITHOUT namespace_separator -> disables namespace enforcement
        self._parser = pyexpat.ParserCreate()
        self._parser.StartElementHandler = self._start_element
        self._parser.EndElementHandler = self._end_element
        self._parser.CharacterDataHandler = self._char_data
        self._stack: list[ET.Element] = []
        self._pending_records: list[ET.Element] = []

    def feed(self, chunk: bytes) -> list[ET.Element]:
        self._parser.Parse(chunk, False)
        recs = self._pending_records
        self._pending_records = []
        return recs

    def close(self) -> list[ET.Element]:
        self._parser.Parse(b"", True)
        recs = self._pending_records
        self._pending_records = []
        return recs

    def _start_element(self, name: str, attrs: dict[str, str]) -> None:
        elem = ET.Element(name, attrs)
        if self._stack:
            self._stack[-1].append(elem)
        self._stack.append(elem)

    def _end_element(self, name: str) -> None:
        if not self._stack:
            return
        elem = self._stack.pop()
        if name.upper() == self.target_tag:
            self._pending_records.append(elem)

    def _char_data(self, data: str) -> None:
        if self._stack:
            top = self._stack[-1]
            if top.text:
                top.text += data
            else:
                top.text = data


def _parse_xml_element(elem: ET.Element) -> Any:
    """Recursively parse an XML Element into a dict or scalar value."""
    children = list(elem)
    if not children:
        return (elem.text or "").strip()

    record: dict[str, Any] = {}
    for child in children:
        tag = child.tag
        parsed_child = _parse_xml_element(child)
        if tag in record:
            if not isinstance(record[tag], list):
                record[tag] = [record[tag]]
            record[tag].append(parsed_child)
        else:
            record[tag] = parsed_child

    for attr_name, attr_val in elem.attrib.items():
        record[f"@{attr_name}"] = attr_val

    return record
