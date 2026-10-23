import tomllib
from pathlib import Path

data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
dependencies = data["project"]["dependencies"]
raise SystemExit(1 if any("==" not in dependency for dependency in dependencies) else 0)
