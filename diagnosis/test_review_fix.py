import pytest
from tally_migrator.config import TallyConfig
from tally_migrator.exceptions import TallyQueryError
from tally_migrator.tally.xml_client import XMLTallyClient


@pytest.mark.parametrize('body', [
    '<ENVELOPE><HEADER><STATUS>0</STATUS></HEADER><BODY/></ENVELOPE>',
    '<ENVELOPE><BODY><LINEERROR>Unknown company</LINEERROR></BODY></ENVELOPE>',
    '',
])
def test_protocol_failures_are_not_empty(body):
    client = XMLTallyClient(TallyConfig())
    with pytest.raises(TallyQueryError):
        list(client._parse_collection_streaming(body, 'LEDGER'))


def test_valid_empty_collection_is_preserved():
    client = XMLTallyClient(TallyConfig())
    assert list(client._parse_collection_streaming(
        '<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><COLLECTION/></BODY></ENVELOPE>',
        'LEDGER')) == []


def test_split_error_marker_is_detected():
    class Response:
        def iter_content(self, **kwargs):
            yield b'<ENVELOPE><BODY><LINE'
            yield b'ERROR>Bad query</LINEERROR></BODY></ENVELOPE>'
    with pytest.raises(TallyQueryError):
        list(XMLTallyClient(TallyConfig())._parse_collection_streaming(Response(), 'LEDGER'))