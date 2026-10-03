"""Create a review diff and isolated source copy; never edits the repository."""
from pathlib import Path
import difflib
import shutil

repo = Path(r'C:\Users\arava\OneDrive\Desktop\Migration\tally_sql')
here = Path(__file__).resolve().parent
rel = Path('tally_migrator/tally/xml_client.py')
old = (repo / 'src' / rel).read_text(encoding='utf-8')
new = old.replace(
    '        elem = self._stack.pop()\n        if name.upper() == self.target_tag:',
    '        elem = self._stack.pop()\n'
    '        # Inspect protocol errors at envelope scope as well as record scope.\n'
    '        upper_name = name.upper()\n'
    '        if upper_name == "LINEERROR" or (\n'
    '            upper_name == "STATUS" and (elem.text or "").strip() == "0"\n'
    '        ):\n'
    '            detail = "".join(elem.itertext()).strip() or "Tally returned STATUS=0"\n'
    '            raise TallyQueryError(self.target_tag, f"Tally extraction failure: {detail}")\n'
    '        if name.upper() == self.target_tag:', 1)
new = new.replace(
    '            if not first_chunk:\n                final_elements = parser.close()',
    '            if first_chunk:\n'
    '                raise TallyQueryError(tag, "Empty HTTP response; extraction not verified")\n'
    '            if not first_chunk:\n                final_elements = parser.close()', 1)
assert new != old
destination = here / 'review-src'
shutil.copytree(repo / 'src', destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
(destination / rel).write_text(new, encoding='utf-8')
diff = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
    fromfile='a/src/tally_migrator/tally/xml_client.py', tofile='b/src/tally_migrator/tally/xml_client.py'))
(here / 'envelope-error-guard.patch').write_text(diff, encoding='utf-8')
print('Prepared isolated review-src and envelope-error-guard.patch. Repository unchanged.')