"""Run ``scripts/install.sh`` against fake ``uv``, ``curl`` and ``sudo`` programs.

The script runs with a PATH holding only ``sh``, ``mktemp``, ``rm``, ``readlink`` and the
selected fake programs, and a private HOME and TMPDIR, so it can reach nothing
real. Every fake program appends its name and arguments to one log; the fake uv
installer, downloaded by fake ``curl -o``, writes a fake ``uv`` to
``$HOME/.local/bin`` the way the official installer does. The fake uv reports the
version in ``FAKE_UV_VERSION`` (default 0.13.7).
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


def fake_uv(log, tool_bin, tool_dir, fail_install=False, short=True):
    """A uv that records calls, reports its directories and installs a stub launcher.

    Like uv on Unix, it installs commands into ``tool_dir/<package>/bin`` and links them
    from ``tool_bin``. The launcher is named after ``XORCHESTRATE_PACKAGE`` (``xorchestrate``
    for the ``xork`` alias, which has no command of its own); with ``short`` the release
    also provides ``xo``. ``tool list`` names each environment in ``tool_dir``.
    """
    launcher = (f"#!{SHELL}\nprintf '%s\\n' '{{\"version\": \"0.0.0\", \"install_source\": \"index\"}}'\n")
    bin_dir, tools = shlex.quote(str(tool_bin)), shlex.quote(str(tool_dir))
    return _logger("uv", log) + f"""
if [ "$1" = --version ]; then printf 'uv %s (fake)\n' "${{FAKE_UV_VERSION:-0.13.7}}"; exit 0; fi
case "$1 $2" in
    "tool dir") if [ "${{3:-}}" = --bin ]; then printf '%s\\n' {bin_dir}; else printf '%s\\n' {tools}; fi ;;
    "tool list")
        for environment in {tools}/*; do
            if [ -d "$environment" ]; then printf '%s v0.0.0\\n' "${{environment##*/}}"; fi
        done
        exit 0 ;;
    "tool install")
        {"exit 2" if fail_install else ""}
        package="${{XORCHESTRATE_PACKAGE:-xorchestrate}}"
        name=$package
        if [ "$package" = xork ]; then name=xorchestrate; fi
        launcher={bin_dir}/"$name"
        mkdir -p {bin_dir} {tools}/"$package"/bin
        if [ -x "$launcher" ]; then echo "already installed"; exit 0; fi
        for command in "$name" {"xo" if short else ""}; do
            printf '%s' {shlex.quote(launcher)} > {tools}/"$package"/bin/"$command"
            chmod 700 {tools}/"$package"/bin/"$command"
            ln -sf {tools}/"$package"/bin/"$command" {bin_dir}/"$command"
        done ;;
    *) exit 64 ;;
esac
"""


class InstallSandbox:
    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.tool_bin = self.home / ".local" / "share" / "tool-bin"
        self.tool_dir = self.home / ".local" / "share" / "tools"
        self.log = self.root / "calls.log"
        self.home.mkdir()
        self.bin.mkdir()
        self.log.touch()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        os.symlink(SHELL, self.bin / "sh")
        for tool in ("mktemp", "rm", "readlink"):
            os.symlink(shutil.which(tool, path=SYSTEM_PATH), self.bin / tool)
        _program(self.bin / "sudo", _logger("sudo", self.log) + "exit 1\n")

    def provide_uv(self, *, fail_install=False, short=True):
        _program(self.bin / "uv", fake_uv(self.log, self.tool_bin, self.tool_dir, fail_install, short))

    def provide_curl(self, *, installs_uv=True, download_fails=False):
        installer = self.root / "uv-installer.sh"
        target = self.home / ".local" / "bin" / "uv"
        body = (f"mkdir -p {shlex.quote(str(target.parent))}\n"
                f"cat > {shlex.quote(str(target))} <<'FAKE_UV'\n#!{SHELL}\nPATH={SYSTEM_PATH}\nexport PATH\n"
                f"{fake_uv(self.log, self.tool_bin, self.tool_dir)}FAKE_UV\nchmod 700 {shlex.quote(str(target))}\n"
                if installs_uv else "exit 0\n")
        installer.write_text(f"PATH={SYSTEM_PATH}\nexport PATH\n" + _logger("uv-installer", self.log) + body)
        _program(self.bin / "curl", _logger("curl", self.log) + ("exit 22\n" if download_fails else
                 'while [ $# -gt 0 ]; do if [ "$1" = -o ]; then output=$2; fi; shift; done\n'
                 f'cat {shlex.quote(str(installer))} > "$output"\n'))

    def run(self, *arguments, env=None):
        environment = {"PATH": str(self.bin), "HOME": str(self.home), "TMPDIR": str(self.tmp), **(env or {})}
        result = subprocess.run([SHELL, str(SCRIPT), *arguments], env=environment, cwd=self.root,
                                capture_output=True, text=True, timeout=60, check=False)
        return result

    def calls(self):
        return [tuple(line.split("\t")) for line in self.log.read_text().splitlines()]
