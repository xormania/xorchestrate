import tempfile
from pathlib import Path

from service.store import record_path

with tempfile.TemporaryDirectory() as temporary:
    try:
        record_path(Path(temporary), "../outside.json")
    except ValueError:
        raise SystemExit(0) from None
raise SystemExit(1)
