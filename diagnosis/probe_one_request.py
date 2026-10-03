"""Send ONE reviewed, GUID-filtered Export XML; no SQL/config loading.

Requires --execute. If it fails with ConnectionError, test the GUID-filtered
Ledger control once with the same session, then once with a fresh session.
Does not save response bodies. Run on the production Windows machine.
"""
import argparse
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import requests
from tally_migrator.tally.xml_client import XMLTallyClient, _build_session
from tally_migrator.config import TallyConfig


def validate(path):
    payload = path.read_bytes()
    root = ET.fromstring(payload)
    if root.findtext('./HEADER/TALLYREQUEST') != 'Export':
        raise ValueError('Only Export requests are permitted')
    if not root.findtext('.//SVCURRENTCOMPANY'):
        raise ValueError('Explicit company is required')
    formula = root.findtext('.//SYSTEM') or ''
    if not formula.startswith('$GUID = "') or not root.findtext('.//FILTER'):
        raise ValueError('Only GUID-filtered requests are permitted')
    return payload


def send(session, url, payload, label, timeout):
    start = time.monotonic()
    try:
        with session.post(url, data=payload, headers={'Content-Type': 'application/xml'},
                          timeout=(5, timeout), stream=True) as response:
            print(label, 'HTTP', response.status_code, 'headers_seconds', round(time.monotonic()-start, 3),
                  {k: response.headers.get(k) for k in ('Server', 'Content-Length', 'Retry-After', 'Connection')})
            response.raise_for_status()
            count = 0
            chunks = []
            for chunk in response.iter_content(65536):
                count += len(chunk)
                if count > 2 * 1024 * 1024:
                    raise ValueError('Response exceeded 2 MiB diagnostic limit')
                chunks.append(chunk)
            body = b''.join(chunks)
            tag = ET.fromstring(payload).findtext('.//COLLECTION/TYPE').upper()
            records = list(XMLTallyClient(TallyConfig())._parse_collection_streaming(body, tag))
            print(label, 'bytes', count, 'records', len(records),
                  'envelope_error_marker', b'<STATUS>0</STATUS>' in body or b'<LINEERROR' in body,
                  'total_seconds', round(time.monotonic()-start, 3))
            if not records:
                print('Zero records is INCONCLUSIVE until company, GUID, and response envelope are verified.')
            return False
    except requests.ConnectionError as exc:
        print(label, type(exc).__name__, str(exc), 'seconds', round(time.monotonic()-start, 3))
        return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--ledger-control', type=Path, required=True)
    p.add_argument('--host', required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--timeout', type=int, default=30)
    p.add_argument('--execute', action='store_true')
    args = p.parse_args()
    payload = validate(args.request)
    ledger = validate(args.ledger_control)
    if not args.execute:
        print('Validated only; no HTTP requests sent. Add --execute to send one variant.')
        return
    url = f'http://{args.host}:{args.port}'
    with _build_session(args.timeout, max_retries=0) as session:
        reset = send(session, url, payload, args.request.stem, args.timeout)
        if reset:
            send(session, url, ledger, 'Ledger-same-session', args.timeout)
    if reset:
        with _build_session(args.timeout, max_retries=0) as fresh:
            send(fresh, url, ledger, 'Ledger-fresh-session', args.timeout)


if __name__ == '__main__':
    main()
