"""Unit tests for XML and ODBC Tally clients using mocks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tally_migrator.config import TallyConfig
from tally_migrator.tally.odbc_client import ODBCTallyClient
from tally_migrator.tally.xml_client import XMLTallyClient


@pytest.fixture
def tally_config():
    return TallyConfig(
        host="localhost",
        port=9000,
        protocol="xml",
        company_name="Test Company",
    )


class TestXMLTallyClient:
    def test_list_collections(self, tally_config):
        client = XMLTallyClient(tally_config)
        collections = client.list_collections()
        assert "Ledger" in collections
        assert "Voucher" in collections

    def test_describe_collection(self, tally_config):
        client = XMLTallyClient(tally_config)
        desc = client.describe_collection("Ledger")
        assert desc["name"] == "Ledger"
        assert desc["key_field"] == "GUID"
        assert desc["alteration_field"] == "ALTERID"

    @patch("requests.Session.post")
    def test_test_connection_success(self, mock_post, tally_config):
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <COMPANY>
                            <NAME>Test Company</NAME>
                            <GUID>1000-2000</GUID>
                        </COMPANY>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        res = client.test_connection()
        assert res["status"] == "ok"
        assert res["company_count"] == 1

    @patch("requests.Session.post")
    def test_fetch_all(self, mock_post, tally_config):
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <LEDGER>
                            <GUID>12345</GUID>
                            <NAME>Cash</NAME>
                            <ALTERID>100</ALTERID>
                        </LEDGER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Ledger"))
        assert len(records) == 1
        assert records[0]["GUID"] == "12345"
        assert records[0]["NAME"] == "Cash"

    @patch("requests.Session.post")
    def test_fetch_since(self, mock_post, tally_config):
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <LEDGER>
                            <GUID>12345</GUID>
                            <NAME>Cash</NAME>
                            <ALTERID>105</ALTERID>
                        </LEDGER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        records = list(client.fetch_since("Ledger", since_marker="100"))
        assert len(records) == 1
        assert records[0]["ALTERID"] == "105"

    @patch("requests.Session.post")
    def test_xml_streaming_iterparse(self, mock_post, tally_config):
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <LEDGER><GUID>g1</GUID><NAME>L1</NAME></LEDGER>
                        <LEDGER><GUID>g2</GUID><NAME>L2</NAME></LEDGER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Ledger"))
        assert len(records) == 2
        assert records[0]["GUID"] == "g1"
        assert records[1]["GUID"] == "g2"

    @patch("requests.Session.post")
    def test_xml_nested_child_collections_parse(self, mock_post, tally_config):
        client = XMLTallyClient(tally_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<ENVELOPE>
            <BODY>
                <DATA>
                    <COLLECTION>
                        <VOUCHER>
                            <GUID>v-100</GUID>
                            <VOUCHERNUMBER>101</VOUCHERNUMBER>
                            <ALLLEDGERENTRIES.LIST>
                                <LEDGERNAME>Cash</LEDGERNAME>
                                <AMOUNT>-100.00</AMOUNT>
                            </ALLLEDGERENTRIES.LIST>
                            <ALLLEDGERENTRIES.LIST>
                                <LEDGERNAME>Sales</LEDGERNAME>
                                <AMOUNT>100.00</AMOUNT>
                            </ALLLEDGERENTRIES.LIST>
                        </VOUCHER>
                    </COLLECTION>
                </DATA>
            </BODY>
        </ENVELOPE>"""
        mock_post.return_value = mock_resp

        records = list(client.fetch_all("Voucher"))
        assert len(records) == 1
        v = records[0]
        assert v["GUID"] == "v-100"
        legs = v["ALLLEDGERENTRIES.LIST"]
        assert isinstance(legs, list)
        assert len(legs) == 2
        assert legs[0]["LEDGERNAME"] == "Cash"
        assert legs[1]["LEDGERNAME"] == "Sales"


class TestODBCTallyClient:
    @patch("pyodbc.connect")
    def test_test_connection_odbc(self, mock_pyodbc_connect):
        cfg = TallyConfig(protocol="odbc", odbc_dsn="TallyODBC_9000")
        client = ODBCTallyClient(cfg)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("Test Company",)]
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc_connect.return_value = mock_conn

        res = client.test_connection()
        assert res["status"] == "ok"
        assert res["companies"] == ["Test Company"]
