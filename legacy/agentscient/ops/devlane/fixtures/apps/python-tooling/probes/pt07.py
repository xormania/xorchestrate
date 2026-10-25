from pathlib import Path

body = Path("tests/test_tooling.py").read_text(encoding="utf-8")
raise SystemExit(1 if 'expected = checksum("fixture")' in body else 0)
