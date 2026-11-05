import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

root=Path(sys.argv[1]);fixture=Path(__file__).resolve().parent/'fixture'
try:
    value=Decimal((root/'answer.txt').read_text().strip())
    ok=value.is_finite() and value==Decimal('7')
    ok=ok and all((root/name).read_bytes()==(fixture/name).read_bytes() for name in ('stats.py','data.csv'))
    reason='ok' if ok else 'wrong median or modified input/tool'
except (OSError,UnicodeError,InvalidOperation):
    ok=False;reason='answer missing or not a number'
print(json.dumps({'pass':ok,'reason':reason}))
