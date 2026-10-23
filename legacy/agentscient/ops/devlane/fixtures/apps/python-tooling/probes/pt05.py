import json
import tempfile
from pathlib import Path

from tool.service import read_records

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "corrupt.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    try:
        read_records(path)
    except json.JSONDecodeError:
        raise SystemExit(0) from None
raise SystemExit(1)
