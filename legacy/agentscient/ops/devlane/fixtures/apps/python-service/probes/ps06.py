from pathlib import Path

body = Path("tests/test_api.py").read_text(encoding="utf-8")
raise SystemExit(1 if 'monkeypatch.setattr(api, "parse_request"' in body else 0)
