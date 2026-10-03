"""Offline characterization tests for the inspected checkout (no SQL/Tally access)."""
from unittest.mock import Mock
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import socket
import struct

import pytest
import requests
from urllib3.exceptions import ProtocolError

from tally_migrator.config import AppConfig, TallyConfig
from tally_migrator.exceptions import TallyConnectionError
from tally_migrator.logging.logger import CollectionStats
from tally_migrator.migration.pipeline import MigrationPipeline
from tally_migrator.tally.xml_client import XMLTallyClient, _build_session


@pytest.mark.parametrize("body", [
    '<ENVELOPE><HEADER><STATUS>0</STATUS></HEADER><BODY><LINEERROR>Bad company</LINEERROR></BODY></ENVELOPE>',
    '<ENVELOPE><BODY><LINEERROR>Bad query</LINEERROR></BODY></ENVELOPE>',
    '<ENVELOPE><BODY><COLLECTION /></BODY></ENVELOPE>',
    '',
])
def test_current_parser_silently_returns_zero(body):
    client = XMLTallyClient(TallyConfig())
    assert list(client._parse_collection_streaming(body, 'LEDGER')) == []
    assert CollectionStats('Ledger').status == 'EMPTY'


def test_real_collection_loop_continues_after_reset():
    cfg = AppConfig()
    cfg.migration.collections = ['Voucher', 'Ledger']
    pipeline = MigrationPipeline(cfg, 'offline', dry_run=True)
    pipeline._setup = Mock()
    pipeline._teardown = Mock()
    pipeline._mapper = Mock()
    pipeline._mapper.get.return_value = SimpleNamespace(change_marker=None)
    client = XMLTallyClient(TallyConfig())
    client._session = Mock()
    response = Mock()
    response.iter_content = lambda **kw: iter([b'<ENVELOPE><LEDGER><GUID>g</GUID></LEDGER></ENVELOPE>'])
    client._session.post.side_effect = [requests.ConnectionError('reset 10054'), response]
    pipeline._tally_client = client
    pipeline._transform_record = lambda raw, mapping: raw
    result = pipeline.run_full_sync()
    assert [c.status for c in result.collections] == ['FAILED', 'OK']
    assert result.collections[1].rows_read == 1
    assert client._session.post.call_count == 2
    assert not result.success


def test_post_reset_is_not_retried_by_current_policy():
    session = _build_session(30)
    try:
        retry = session.get_adapter('http://localhost').max_retries
        assert 'POST' not in retry.allowed_methods
        with pytest.raises(ProtocolError):
            retry.increment(method='POST', error=ProtocolError('reset', ConnectionResetError(10054)))
    finally:
        session.close()


def test_guid_only_voucher_request_still_fetches_all_children():
    xml = XMLTallyClient(TallyConfig())._build_collection_request('Voucher', fields=['GUID'])
    assert xml.count('<FETCH>') == 9
    assert '<FETCH>ALLLEDGERENTRIES.*</FETCH>' in xml
    assert '<FETCH>ACCOUNTINGALLOCATIONS.*</FETCH>' in xml


def test_real_session_survives_local_tcp_reset():
    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            Handler.calls += 1
            if Handler.calls == 1:
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('hh', 1, 0))
                self.connection.close()
                self.close_connection = True
                return
            body = b'<ENVELOPE><LEDGER><GUID>g</GUID></LEDGER></ENVELOPE>'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = XMLTallyClient(TallyConfig(host='127.0.0.1', port=server.server_port, timeout_seconds=3))
    try:
        with pytest.raises(TallyConnectionError):
            list(client.fetch_all('Voucher'))
        same_session = client._session
        assert len(list(client.fetch_all('Ledger'))) == 1
        assert client._session is same_session
        client.close()
        assert len(list(client.fetch_all('Ledger'))) == 1
        assert client._session is not same_session
        assert Handler.calls == 3
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
