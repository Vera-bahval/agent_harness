import json
from pathlib import Path
import sys

root=Path(sys.argv[1])
try:
    answer=(root/'answer.txt').read_text().strip()
    ok=answer=='app/helpers/dates.py'
    reason='ok' if ok else 'expected definition path app/helpers/dates.py'
except (OSError,UnicodeError):
    ok=False;reason='answer.txt missing or invalid'
print(json.dumps({'pass':ok,'reason':reason}))
