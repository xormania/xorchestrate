import tempfile
from pathlib import Path

from service.audit import log_request

secret = "PS03_DO_NOT_LOG"
request = {"headers": {"authorization": f"Bearer {secret}"}}
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "request.log"
    log_request(request, path)
    planted = secret in path.read_text(encoding="utf-8")
raise SystemExit(1 if planted else 0)
