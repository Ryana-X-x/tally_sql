"""Generate filtered export XML only. Never sends HTTP or connects to SQL.

Run with the production checkout's src on PYTHONPATH:
python build_bounded_requests.py --company "Exact company" --voucher-guid GUID --out requests
"""
import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from tally_migrator.config import TallyConfig
from tally_migrator.tally.xml_client import XMLTallyClient, KNOWN_COLLECTIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--company', required=True)
    parser.add_argument('--voucher-guid', required=True)
    parser.add_argument('--ledger-guid', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    for guid in (args.voucher_guid, args.ledger_guid):
        if not re.fullmatch(r'[A-Za-z0-9-]{1,128}', guid):
            parser.error('GUID must contain only letters, digits, and hyphens')
    client = XMLTallyClient(TallyConfig(company_name=args.company))
    full = ET.fromstring(client._build_collection_request('Voucher'))
    all_fetches = [e.text for e in full.findall('.//COLLECTION/FETCH')]
    extras = all_fetches[len(KNOWN_COLLECTIONS['Voucher']['fields']):]
    variants = {'A-header': [], **{f'{chr(66+i)}-{f.split(".")[0]}': [f] for i, f in enumerate(extras[:5])},
                'G-full-current': extras,
                **{f'{chr(72+i)}-{f.split(".")[0]}': [f] for i, f in enumerate(extras[5:])}}
    args.out.mkdir(parents=True, exist_ok=True)
    for label, fetches in variants.items():
        root = ET.fromstring(client._build_collection_request(
            'Voucher', filter_expr=f'$GUID = "{args.voucher_guid}"'))
        coll = root.find('.//COLLECTION')
        for node in list(coll.findall('FETCH')):
            coll.remove(node)
        for field in KNOWN_COLLECTIONS['Voucher']['fields'] + fetches:
            ET.SubElement(coll, 'FETCH').text = field
        ET.ElementTree(root).write(args.out / f'{label}.xml', encoding='utf-8')
    ledger = client._build_collection_request('Ledger', fields=['GUID', 'NAME'],
        filter_expr=f'$GUID = "{args.ledger_guid}"')
    (args.out / 'ledger-control.xml').write_text(ledger, encoding='utf-8')
    print(f'Generated {len(variants)} Voucher requests and one Ledger control. No requests sent.')


if __name__ == '__main__':
    main()
