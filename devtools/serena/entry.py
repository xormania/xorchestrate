"""Container-only setup for the separately installed Serena MCP process."""

import os
import shutil
from pathlib import Path


def main():
    from process_audit import install

    Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
    target = Path(os.environ["SERENA_HOME"]) / "language_servers/static/PhpactorServer/phpactor.phar"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile("/opt/phpactor.phar", target)
    install()

    # Invoke the installed console entrypoint in this interpreter: execvp would
    # replace it and discard the audit hook before any REPL code could run.
    from serena.cli import top_level

    top_level(
        args=[
            "start-mcp-server",
            "--project",
            "/workspace",
            "--agent-interface",
            "REPL",
            "--context",
            "desktop-app",
            "--mode",
            "editing",
            "--mode",
            "no-onboarding",
            "--enable-web-dashboard",
            "False",
            "--open-web-dashboard",
            "False",
            "--enable-gui-log-window",
            "False",
            "--tool-timeout",
            "90",
        ],
        prog_name="serena",
    )


if __name__ == "__main__":
    main()
