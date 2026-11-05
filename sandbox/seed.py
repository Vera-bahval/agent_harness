"""Trusted fixture initialization, before the manager accepts any user operation."""
import base64
import io
import os
from pathlib import Path
import sys
import tarfile

mode = sys.argv[1]
data = base64.b64decode(''.join(sys.argv[2:]), validate=True)
with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
    # The manager builds this from an allowlist of trusted fixture directories.
    archive.extractall('/work', filter='data')
if mode == 'ro':
    for path in Path('/work').rglob('*'):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod('/work', 0o555)
