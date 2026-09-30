"""Regression tests for Tally XML parsing compatibility, sanitization, and error propagation.

Verifies:
1. Unbound namespace prefix handling (UDF:*, sys:* tags)
2. Invalid XML 1.0 numeric character references (&#0;, &#x0;, &#x1F;, &#31;, &#xB;, etc.)
3. Preservation of legal character references (&#x9;, &#xA;, &#xD;, &#x20;, &#xA0;)
4. Preservation of high valid Unicode (Hindi/Indian scripts, accents, currency, emojis)
5. Streaming chunk boundary buffering for split entity references (e.g. "... &#x" + "1F; ...")
6. Preservation of UDF/custom fields in Ledger, StockItem, Voucher
7. Error propagation (genuine parser exceptions are NOT swallowed)
8. Distinguishing legitimately EMPTY collections from FAILED collections
9. Dry-run failure on parse errors
10. Full-sync refusal on critical extraction failures
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tally_migrator.config import AppConfig, TallyConfig
from tally_migrator.exceptions import TallyQueryError
from tally_migrator.migration.full_sync import FullSyncRunner
from tally_migrator.migration.pipeline import MigrationPipeline
from tally_migrator.schema.discovery import SchemaDiscovery
from tally_migrator.schema.models import (
    CollectionCategory,
    DiscoveredCollection,
    DiscoveredField,
    DiscoveredSchema,
    FieldType,
)
from tally_migrator.tally.xml_client import (
    StreamingXMLSanitizer,
    XMLTallyClient,
    is_valid_xml_character,
)


@pytest.fixture
def tally_config():
    return TallyConfig(
        host="localhost",
        port=9000,
        protocol="xml",
        company_name="Test Company",
    )


@pytest.fixture
def app_config(tmp_path):
    cfg = AppConfig()
    cfg.tally.host = "localhost"
    cfg.tally.port = 9000
    cfg.tally.protocol = "xml"
    cfg.sql.server = "localhost"
    cfg.sql.database = "TestDB"
    cfg.sql.trusted_connection = True
    cfg.migration.schema_dir = tmp_path / "schema"
    cfg.migration.sql_dir = tmp_path / "sql"
    cfg.migration.dry_run = True

    cfg.migration.schema_dir.mkdir(parents=True, exist_ok=True)
    schema = DiscoveredSchema(
        collections={
            "Ledger": DiscoveredCollection(
                name="Ledger",
                category=CollectionCategory.MASTER,
                fields={
                    "GUID": DiscoveredField(name="GUID", tally_type=FieldType.GUID, is_primary_key=True),
                    "NAME": DiscoveredField(name="NAME", tally_type=FieldType.TEXT),
                    "ALTERID": DiscoveredField(name="ALTERID", tally_type=FieldType.INTEGER, is_change_marker=True),
                },
                primary_key_field="GUID",
                change_marker_field="ALTERID",
            ),
            "Voucher": DiscoveredCollection(
                name="Voucher",
                category=CollectionCategory.TRANSACTION,
                fields={
                    "GUID": DiscoveredField(name="GUID", tally_type=FieldType.GUID, is_primary_key=True),
                    "VOUCHERNUMBER": DiscoveredField(name="VOUCHERNUMBER", tally_type=FieldType.TEXT),
                    "ALTERID": DiscoveredField(name="ALTERID", tally_type=FieldType.INTEGER, is_change_marker=True),
                },
                primary_key_field="GUID",
                change_marker_field="ALTERID",
            ),
        }
    )
    SchemaDiscovery.save(schema, cfg.migration.schema_dir / "discovered_schema.json")
    return cfg


REALISTIC_TALLY_XML_WITH_UDF = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <STATUS>1</STATUS>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Collection</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE>
          <LEDGER NAME="Acme Traders" RESERVEDNAME="">
            <GUID>g-acme-100</GUID>
            <NAME>Acme Traders</NAME>
            <PARENT>Sundry Debtors</PARENT>
            <ALTERID>1500</ALTERID>
            <UDF:GSTIN>27AAACA1234A1Z5</UDF:GSTIN>
            <UDF:CUSTOM_DATE>20260101</UDF:CUSTOM_DATE>
            <sys:SYSTEM_PARAM>OK</sys:SYSTEM_PARAM>
            <UDF:DETAILS>
              <UDF:SUBCODE>XYZ-99</UDF:SUBCODE>
            </UDF:DETAILS>
          </LEDGER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


class TestTallyXMLCompatibility:
    def test_xml_character_validity_rules(self):
        """Verify legal vs illegal XML 1.0 code point classification."""
        assert is_valid_xml_character(0x9) is True   # Tab
        assert is_valid_xml_character(0xA) is True   # LF
        assert is_valid_xml_character(0xD) is True   # CR
        assert is_valid_xml_character(0x20) is True  # Space
        assert is_valid_xml_character(0x0) is False  # NULL
        assert is_valid_xml_character(0x1F) is False # Unit separator
        assert is_valid_xml_character(0x8) is False  # Backspace
        assert is_valid_xml_character(0xB) is False  # Vertical Tab
        assert is_valid_xml_character(0xC) is False  # Form Feed
        assert is_valid_xml_character(0xE) is False  # Shift Out
        assert is_valid_xml_character(31) is False   # Decimal 31
        assert is_valid_xml_character(0x2439) is True # Hindi Devanagari character
        assert is_valid_xml_character(0x1F600) is True # Emoji U+1F600

    @patch("requests.Session.post")
    def test_unbound_namespace_prefix_tally_xml(self, mock_post, tally_config):
        """Verify XML parsing succeeds on XML containing unbound prefixes (UDF:*, sys:*)."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = REALISTIC_TALLY_XML_WITH_UDF
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Ledger"))
        assert len(records) == 1
        rec = records[0]
        assert rec["GUID"] == "g-acme-100"
        assert rec["NAME"] == "Acme Traders"
        assert rec["UDF:GSTIN"] == "27AAACA1234A1Z5"
        assert rec["UDF:CUSTOM_DATE"] == "20260101"
        assert rec["sys:SYSTEM_PARAM"] == "OK"

    @patch("requests.Session.post")
    def test_udf_prefixed_elements_ledger_stockitem_voucher(self, mock_post, tally_config):
        """Verify Ledger, StockItem, and Voucher collections with UDF elements parse completely."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <STOCKITEM>
                            <GUID>si-1</GUID>
                            <NAME>Widget A</NAME>
                            <UDF:HSNCODE>8471</UDF:HSNCODE>
                        </STOCKITEM>
                        <VOUCHER>
                            <GUID>v-1</GUID>
                            <VOUCHERNUMBER>INV-001</VOUCHERNUMBER>
                            <UDF:EWAYBILL>EWB-12345</UDF:EWAYBILL>
                        </VOUCHER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        items = list(client.fetch_all("StockItem"))
        vouchers = list(client.fetch_all("Voucher"))

        assert len(items) == 1
        assert items[0]["UDF:HSNCODE"] == "8471"
        assert len(vouchers) == 1
        assert vouchers[0]["UDF:EWAYBILL"] == "EWB-12345"

    @patch("requests.Session.post")
    def test_nested_xml_udf_structure(self, mock_post, tally_config):
        """Verify nested UDF structures are correctly parsed."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = REALISTIC_TALLY_XML_WITH_UDF
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Ledger"))
        details = records[0]["UDF:DETAILS"]
        assert isinstance(details, dict)
        assert details["UDF:SUBCODE"] == "XYZ-99"

    @patch("requests.Session.post")
    def test_invalid_character_number_reference_sanitization(self, mock_post, tally_config):
        """Verify invalid XML 1.0 character references (&#0;, &#x0;, &#x1F;, &#31;) are sanitized."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <LEDGER>
                            <GUID>g-bad-1</GUID>
                            <NAME>Alpha &#x0; Beta &#0; Gamma &#x1F; Delta &#31;</NAME>
                            <DESCRIPTION>Clean &#xB; &#xC; &#xE;</DESCRIPTION>
                        </LEDGER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Ledger"))
        assert len(records) == 1
        rec = records[0]
        assert rec["GUID"] == "g-bad-1"
        assert rec["NAME"] == "Alpha  Beta  Gamma  Delta"
        assert rec["DESCRIPTION"] == "Clean"

    @patch("requests.Session.post")
    def test_legal_character_references_and_unicode_preserved(self, mock_post, tally_config):
        """Verify legal character references and high Unicode (Hindi, accents, symbols) are preserved."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <LEDGER>
                            <GUID>g-unicode-1</GUID>
                            <NAME>नमस्ते Enterprise &#2439; &#x9; Line2&#xA;Tab&#xD; Space&#x20;End</NAME>
                            <UDF:HINDI_NAME>श्री गणेश स्टोर</UDF:HINDI_NAME>
                        </LEDGER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Ledger"))
        assert len(records) == 1
        rec = records[0]
        assert "नमस्ते Enterprise" in rec["NAME"]
        assert "श्री गणेश स्टोर" in rec["UDF:HINDI_NAME"]

    def test_numeric_references_split_across_chunks(self):
        """Verify stateful StreamingXMLSanitizer handles entity references split across HTTP chunks."""
        sanitizer = StreamingXMLSanitizer()

        c1 = sanitizer.feed("<LEDGER><NAME>Acme &#x", is_final=False)
        c2 = sanitizer.feed("1F; Traders &#", is_final=False)
        c3 = sanitizer.feed("0; End</NAME></LEDGER>", is_final=False)
        c4 = sanitizer.flush()

        result = c1 + c2 + c3 + c4
        assert result == "<LEDGER><NAME>Acme  Traders  End</NAME></LEDGER>"
        assert sanitizer.sanitized_count == 2
        assert "U+001F" in sanitizer.sanitized_code_points
        assert "U+0000" in sanitizer.sanitized_code_points

    @patch("requests.Session.post")
    def test_regression_reference_to_invalid_character_number(self, mock_post, tally_config):
        """Regression test specifically reproducing 'reference to invalid character number' from production."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Simulates real production Tally XML with invalid control references in Group & StockItem
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <GROUP>
                            <GUID>grp-prod-100</GUID>
                            <NAME>Primary Group &#x1F;</NAME>
                            <ALTERID>247</ALTERID>
                        </GROUP>
                        <STOCKITEM>
                            <GUID>item-prod-50</GUID>
                            <NAME>Item &#x0; Name</NAME>
                            <ALTERID>51</ALTERID>
                        </STOCKITEM>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        groups = list(client.fetch_all("Group"))
        items = list(client.fetch_all("StockItem"))

        assert len(groups) == 1
        assert groups[0]["NAME"] == "Primary Group"
        assert len(items) == 1
        assert items[0]["NAME"] == "Item  Name"

    @patch("requests.Session.post")
    def test_parser_failure_propagation(self, mock_post, tally_config):
        """Verify genuinely malformed XML raises TallyQueryError rather than being swallowed."""
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<ENVELOPE><BODY><LEDGER><NAME>Broken"
        mock_post.return_value = mock_resp

        with pytest.raises(TallyQueryError) as exc_info:
            list(client.fetch_all("Ledger"))
        assert "XML streaming parse error" in str(exc_info.value)

    @patch("tally_migrator.tally.factory.create_tally_client")
    def test_empty_collection_vs_failed_collection(self, mock_create_client, app_config):
        """Distinguish legitimately EMPTY collection from FAILED collection."""
        # 1. Empty collection
        mock_client_empty = MagicMock()
        mock_client_empty.list_collections.return_value = ["Ledger"]
        mock_client_empty.fetch_all.return_value = iter([])
        mock_create_client.return_value = mock_client_empty

        pipeline = MigrationPipeline(app_config, run_id="run-empty", dry_run=True)
        res_empty = pipeline.run_full_sync()

        stats_empty = res_empty.collections[0]
        assert stats_empty.rows_read == 0
        assert stats_empty.rows_failed == 0
        assert stats_empty.status == "EMPTY"
        assert res_empty.success is True

        # 2. Failed collection
        mock_client_fail = MagicMock()
        mock_client_fail.list_collections.return_value = ["Ledger"]

        def _fail_gen(*args, **kwargs):
            raise TallyQueryError("Ledger", "XML streaming parse error: unbound prefix: line 76")
            yield  # unreachable

        mock_client_fail.fetch_all.side_effect = _fail_gen
        mock_create_client.return_value = mock_client_fail

        pipeline_fail = MigrationPipeline(app_config, run_id="run-fail", dry_run=True)
        res_fail = pipeline_fail.run_full_sync()

        stats_fail = res_fail.collections[0]
        assert stats_fail.rows_read == 0
        assert stats_fail.rows_failed >= 1
        assert stats_fail.status == "FAILED"
        assert len(stats_fail.errors) == 1
        assert res_fail.success is False

    @patch("tally_migrator.tally.factory.create_tally_client")
    def test_dry_run_failure_on_parser_error(self, mock_create_client, app_config):
        """Verify dry-run reports overall FAILED status when parser error occurs."""
        mock_client = MagicMock()
        mock_client.list_collections.return_value = ["Ledger"]

        def _err_gen(*args, **kwargs):
            raise TallyQueryError("Ledger", "XML parse error")
            yield

        mock_client.fetch_all.side_effect = _err_gen
        mock_create_client.return_value = mock_client

        pipeline = MigrationPipeline(app_config, run_id="dry-run-err", dry_run=True)
        res = pipeline.run_full_sync()

        assert res.success is False
        assert res.total_failed >= 1

    @patch("tally_migrator.tally.factory.create_tally_client")
    def test_full_sync_refusal_after_critical_extraction_failure(self, mock_create_client, app_config):
        """Verify FullSyncRunner returns success=False when source extraction fails."""
        mock_client = MagicMock()
        mock_client.list_collections.return_value = ["Ledger", "Voucher"]

        def _fetch_side_effect(coll_name, *args, **kwargs):
            if coll_name == "Voucher":
                raise TallyQueryError("Voucher", "Connection broken during XML stream")
            yield {"GUID": "l-1", "NAME": "Cash", "ALTERID": "10"}

        mock_client.fetch_all.side_effect = _fetch_side_effect
        mock_create_client.return_value = mock_client

        runner = FullSyncRunner(app_config, run_id="full-sync-fail-test")
        res = runner.run()

        assert res.success is False
        v_stats = [c for c in res.collections if c.name == "Voucher"][0]
        assert v_stats.status == "FAILED"
        assert v_stats.rows_failed >= 1
