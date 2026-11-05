import json
from pathlib import Path
import sys

root=Path(sys.argv[1])
fixture=Path(__file__).resolve().parent/'fixture'
try:
    original=(fixture/'README.md').read_bytes()
    expected=original.replace(b'pip install example-client==1.2',b'pip install example-client==1.3',1)
    ok=(root/'README.md').read_bytes()==expected
    for source in fixture.rglob('*'):
        if source.is_file() and source.name!='README.md':
            ok=ok and (root/source.relative_to(fixture)).read_bytes()==source.read_bytes()
    reason='ok' if ok else 'unexpected bytes changed or Install version not updated'
except OSError:
    ok=False;reason='required file missing'
print(json.dumps({'pass':ok,'reason':reason}))
