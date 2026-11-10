"""Run ``scripts/install.sh`` against fake ``uv``, ``curl`` and ``sudo`` programs.

The script runs with a PATH holding only ``sh`` and the selected fake programs,
and a private HOME, so it can reach nothing real. Every fake program appends its
name and arguments to one log; the fake uv installer, served by fake ``curl``,
writes a fake ``uv`` to ``$HOME/.local/bin`` the way the official installer does.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "install.sh"
SYSTEM_PATH = "/usr/bin:/bin"
SHELL = shutil.which("sh", path=SYSTEM_PATH) or "/bin/sh"


def _program(path, body):
    path.write_text(f"#!{SHELL}\nPATH={SYSTEM_PATH}\nexport PATH\n{body}")
    path.chmod(0o700)


def _logger(name, log):
    return f"printf '%s' {shlex.quote(name)} >> {shlex.quote(str(log))}\n" \
           f"for argument in \"$@\"; do printf '\\t%s' \"$argument\" >> {shlex.quote(str(log))}; done\n" \
           f"printf '\\n' >> {shlex.quote(str(log))}\n"


def fake_uv(log, tool_bin, fail_install=False):
    """A uv that records calls, reports ``tool_bin`` and installs a stub launcher.

    The launcher is named after ``XORCHESTRATE_PACKAGE``, as uv names it after the package.
    """
    launcher = (f"#!{SHELL}\nprintf '%s\\n' '{{\"version\": \"0.0.0\", \"install_source\": \"index\"}}'\n")
    return _logger("uv", log) + f"""
case "$1 $2" in
    "tool dir") printf '%s\\n' {shlex.quote(str(tool_bin))} ;;
    "tool install")
        {"exit 2" if fail_install else ""}
        launcher={shlex.quote(str(tool_bin))}/"${{XORCHESTRATE_PACKAGE:-xorchestrate}}"
        mkdir -p {shlex.quote(str(tool_bin))}
        if [ -x "$launcher" ]; then echo "already installed"; exit 0; fi
        printf '%s' {shlex.quote(launcher)} > "$launcher"
        chmod 700 "$launcher" ;;
    *) exit 64 ;;
esac
"""


class InstallSandbox:
    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.tool_bin = self.home / ".local" / "share" / "tool-bin"
        self.log = self.root / "calls.log"
        self.home.mkdir()
        self.bin.mkdir()
        self.log.touch()
        os.symlink(SHELL, self.bin / "sh")
        _program(self.bin / "sudo", _logger("sudo", self.log) + "exit 1\n")

    def provide_uv(self, *, fail_install=False):
        _program(self.bin / "uv", fake_uv(self.log, self.tool_bin, fail_install))

    def provide_curl(self, *, installs_uv=True):
        installer = self.root / "uv-installer.sh"
        target = self.home / ".local" / "bin" / "uv"
        body = (f"mkdir -p {shlex.quote(str(target.parent))}\n"
                f"cat > {shlex.quote(str(target))} <<'FAKE_UV'\n#!{SHELL}\nPATH={SYSTEM_PATH}\nexport PATH\n"
                f"{fake_uv(self.log, self.tool_bin)}FAKE_UV\nchmod 700 {shlex.quote(str(target))}\n"
                if installs_uv else "exit 0\n")
        installer.write_text(f"PATH={SYSTEM_PATH}\nexport PATH\n" + _logger("uv-installer", self.log) + body)
        _program(self.bin / "curl", _logger("curl", self.log) + f"cat {shlex.quote(str(installer))}\n")

    def run(self, *arguments, env=None):
        environment = {"PATH": str(self.bin), "HOME": str(self.home), **(env or {})}
        result = subprocess.run([SHELL, str(SCRIPT), *arguments], env=environment, cwd=self.root,
                                capture_output=True, text=True, timeout=60, check=False)
        return result

    def calls(self):
        return [tuple(line.split("\t")) for line in self.log.read_text().splitlines()]
