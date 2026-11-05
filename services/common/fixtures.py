"""Server-owned fixture IDs. Only these sources may be provisioned."""
from pathlib import Path
SOURCES={'empty':None,**{f'B{i}_{j}':Path(f'/app/fixtures/B{i}_{j}') for i in range(1,6) for j in range(1,4)}}
