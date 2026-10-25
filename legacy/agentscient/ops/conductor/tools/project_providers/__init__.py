"""Reviewed project integrations; configuration never supplies imports or commands."""

import json
from pathlib import Path

from instrument import refuse

from project_providers.symfony import Provider


def load(path=None):
    if path is None:
        return Provider({})
    try:
        config = json.loads(Path(path).read_text())
        if (not isinstance(config, dict) or type(config.get("version")) is not int or config.get("version") != 1
                or config.get("provider") != "symfony"
                or set(config) != {"version", "provider", "php", "environment"}):
            raise ValueError("expected version, provider, php and environment")
        return Provider(config)
    except (ValueError, OSError) as error:
        refuse("project-config", "a reviewed integration configuration", str(error),
               "use the documented provider configuration; commands and imports are not configuration")
