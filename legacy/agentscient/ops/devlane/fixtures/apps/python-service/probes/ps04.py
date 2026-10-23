import tempfile
from pathlib import Path

from service.api import handle

with tempfile.TemporaryDirectory() as temporary:
    response = handle("not json", Path(temporary))
raise SystemExit(1 if response.get("ok") is True else 0)
