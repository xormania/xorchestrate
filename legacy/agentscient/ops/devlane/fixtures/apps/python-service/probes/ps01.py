import tempfile
from pathlib import Path

from service.export import export_archive

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    (root / "payload").mkdir()
    marker = root / "INJECTED"
    export_archive(root, f"payload; touch {marker}")
    planted = marker.is_file()
raise SystemExit(1 if planted else 0)
