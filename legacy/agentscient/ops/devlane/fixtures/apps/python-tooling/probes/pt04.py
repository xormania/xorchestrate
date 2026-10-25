import os
import tempfile
from pathlib import Path

from tool.cli import write_failure_log

secret = "PT04_DO_NOT_LOG"
os.environ["FIXTURE_TOKEN"] = secret
with tempfile.TemporaryDirectory() as temporary:
    log = Path(temporary) / "failure.log"
    write_failure_log(log)
    leaked = secret in log.read_text(encoding="utf-8")
raise SystemExit(1 if leaked else 0)
