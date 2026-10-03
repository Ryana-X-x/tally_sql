# Read-only diagnostic artifacts

Inspected checkout: C:\Users\arava\OneDrive\Desktop\Migration\tally_sql,
HEAD da3a8f8. No ZIP was available; the user supplied the checkout path again.
No production HTTP requests, SQL operations, full sync, or repository edits were performed.

## Run the bounded comparison on the production Windows machine

Copy this diagnosis directory to that machine if necessary. Choose one KNOWN
Voucher GUID and one KNOWN Ledger GUID in the SAME explicitly named company.
Use GUIDs from existing records/logs rather than issuing an unbounded fetch.

PowerShell, using that machine's working Python environment:

```powershell
Set-Location 'C:\Users\arava\OneDrive\Desktop\Migration\tally_sql'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$diagnosticDir = 'C:\Users\arava\OneDrive\Documents\ChatGPT\Aravali Data Analytics project\diagnosis'
python "$diagnosticDir\build_bounded_requests.py" --company 'EXACT COMPANY' --voucher-guid 'KNOWN-VOUCHER-GUID' --ledger-guid 'KNOWN-LEDGER-GUID' --out "$diagnosticDir\requests"
```

Inspect the generated XML. A is header-only; B-F each add one requested child;
G contains all EIGHT current child directives; H-J test the three additional
directives. The production builder normally adds these even for fields=['GUID'];
the generator deliberately replaces FETCH elements in diagnostic XML only.

Start with A and confirm exactly one Voucher is returned. Send one variant at
a time, during a quiet period. Change only the request filename:

```powershell
python "$diagnosticDir\probe_one_request.py" --request "$diagnosticDir\requests\A-header.xml" --ledger-control "$diagnosticDir\requests\ledger-control.xml" --host aravali.technosky.cloud --port 9938 --execute
```

The probe has zero automatic retries. After a connection error it sends a small
Ledger control using the same session, then a fresh session. It prints HTTP
status, selected response headers, header/body timings, byte count, record count,
and error-marker presence. It never loads SQL configuration or writes SQL.
Without --execute it validates XML and sends nothing. Responses are capped at
2 MiB and are not saved. A GUID filter bounds output, but Tally may still scan
the collection to evaluate the filter; this is not a guaranteed server-work limit.

Do not run all variants automatically or repeat full dataset queries. If a
variant resets, inspect Tally, reverse-proxy, firewall and Windows event logs at
the same timestamp before further probes. Stop if Tally becomes unhealthy.
Zero records is inconclusive until the company, GUID and envelope are verified.

## Review patch and tests

envelope-error-guard.patch is a proposed minimal parser correction, NOT a fix
for the production reset. It detects LINEERROR/STATUS=0 outside the record and
rejects an empty HTTP body. It preserves a legitimate empty collection.
review-src contains an isolated patched copy; original source is untouched.

Tests run with bundled Python 3.12.14 and the checkout's pure-Python dependencies:
- 8 characterization/local TCP reset tests passed against original source.
- 5 protocol-error regression tests passed against isolated patched source.
- Original suite: 109 passed; 1 failed because pyodbc is unavailable in this runtime.
- Patched suite: 109 passed; that same ODBC test deselected.

Revalidate on production-compatible Python 3.13 before applying the patch.
Existing A11 test embeds LINEERROR INSIDE Voucher and therefore does not cover
normal envelope-level errors. Additional fixtures should cover wrong company,
unexpected XML response shape, malformed/truncated XML, and errors after records.

The patch does not fully validate every successful Tally response shape. A
well-formed but unexpected envelope can still yield zero records; raw response
shape diagnostics remain necessary. Do not call a collection truly empty merely
because no target tags were extracted.