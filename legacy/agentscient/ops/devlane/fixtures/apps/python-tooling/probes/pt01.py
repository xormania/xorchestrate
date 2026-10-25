from pathlib import Path

from tool.cli import run_helper

marker = Path("pt01-shell-marker")
marker.unlink(missing_ok=True)
run_helper(f"literal; touch {marker}")
raise SystemExit(1 if marker.exists() else 0)
