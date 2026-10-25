"""Check native operator clients; explicit updates preserve installation ownership.

Official installation sources, verified 2026-10-02:
https://developers.openai.com/codex/cli
https://code.claude.com/docs/en/setup
https://docs.x.ai/build/overview
https://docs.x.ai/build/cli/reference
https://antigravity.google/docs/cli/install/

Agy is an operator client, not an Agentscient managed harness adapter.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

CLIENTS = ("grok", "codex", "claude", "agy")
PACKAGES = {"codex": "@openai/codex", "claude": "@anthropic-ai/claude-code"}
INSTALLERS = {
    "grok": "https://x.ai/cli/install.sh",
    "codex": "https://chatgpt.com/codex/install.sh",
    "claude": "https://claude.ai/install.sh",
    "agy": "https://antigravity.google/cli/install.sh",
}
AGY_RELEASES = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
VERSION = re.compile(r"(?<![\w.])(\d+\.\d+\.\d+(?:-[A-Za-z0-9._]+)?)(?![\w.])")


class MaintenanceError(Exception):
    pass


def run(argv, *, timeout=30):
    # Version queries must not trigger Claude's background updater. No login or
    # model command is used, and raw child output (possibly credentials) is hidden.
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1", "NO_COLOR": "1"}
    try:
        result = subprocess.run(
            [str(part) for part in argv], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MaintenanceError(f"{Path(argv[0]).name}: unavailable or timed out") from exc
    if result.returncode:
        raise MaintenanceError(f"{Path(argv[0]).name}: command failed (exit {result.returncode})")
    return result.stdout.strip()


def fetch(url, *, limit=1024 * 1024):
    """Public release metadata / official installer only; never add credentials."""
    try:
        with urlopen(Request(url, headers={"User-Agent": "agentscient-harness-check"}), timeout=20) as response:
            if urlparse(response.url).scheme != "https":
                raise MaintenanceError("Release server redirected outside HTTPS")
            data = response.read(limit + 1)
    except (OSError, URLError) as exc:
        raise MaintenanceError("Public release lookup failed; check connectivity and retry") from exc
    if len(data) > limit:
        raise MaintenanceError("Release response exceeds the size limit")
    return data


def parse_version(text):
    found = VERSION.search(text)
    if not found:
        raise MaintenanceError("Version was not recognized; no freshness claim is possible")
    return found.group(1)


def native_paths(name):
    home = Path.home()
    paths = [home / ".local/bin" / name]
    if name == "grok":
        paths.append(Path(os.environ.get("GROK_BIN_DIR", home / ".grok/bin")) / name)
    return paths


def executable_paths(name):
    found = {}
    for directory in os.get_exec_path():
        candidate = Path(directory or ".") / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            found.setdefault(candidate.resolve(), candidate.absolute())
    # A native installation off PATH must not be overwritten as if absent.
    for candidate in native_paths(name):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            found.setdefault(candidate.resolve(), candidate.absolute())
    return list(found.values())


def npm_directory(name, executable):
    package = PACKAGES.get(name)
    if not package or not shutil.which("npm"):
        return None
    root = Path(run(["npm", "root", "--global"]))
    directory = root / package
    try:
        metadata = json.loads((directory / "package.json").read_text())
    except (OSError, ValueError):
        return None
    if metadata.get("name") == package and executable.resolve().is_relative_to(directory.resolve()):
        return directory
    return None


def installation(name, paths):
    if not paths:
        return "missing", None
    if len(paths) > 1:
        raise MaintenanceError("Multiple distinct installations found; remove the PATH conflict before updating")
    executable = paths[0]
    directory = npm_directory(name, executable)
    if directory:
        return "npm", directory
    resolved = executable.resolve()
    if any(part in {"Cellar", "Caskroom"} for part in resolved.parts):
        if "Caskroom" in resolved.parts:
            package = resolved.parts[resolved.parts.index("Caskroom") + 1]
            allowed = {"codex": {"codex"}, "claude": {"claude-code", "claude-code@latest"}}
            if package in allowed.get(name, set()):
                return "homebrew", package
        return "unknown", None
    # Only ordinary binaries at documented native locations can self-update.
    # Shell/npm shims and custom launchers need their original installation owner.
    try:
        with resolved.open("rb") as stream:
            magic = stream.read(4)
    except OSError as exc:
        raise MaintenanceError("Cannot read installed executable") from exc
    is_binary = magic == b"\x7fELF" or magic in {
        b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe",
    }
    if executable in native_paths(name) and is_binary and resolved.is_relative_to(Path.home()):
        if name == "claude" and executable.is_symlink():
            if not resolved.is_relative_to(Path.home() / ".local/share/claude/versions"):
                return "unknown", None
        return "native", None
    return "unknown", None


def installed_version(name, executable):
    args = [executable, "--no-auto-update", "version"] if name == "grok" else [executable, "--version"]
    return parse_version(run(args))


def agy_manifest():
    """Endpoint and platform spelling come from Google's published installer."""
    os_name = {"Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if not os_name or not arch:
        raise MaintenanceError("Agy release lookup supports Linux/macOS x64/arm64")
    suffix = "_musl" if os_name == "linux" and list(Path("/lib").glob("libc.musl-*.so.1")) else ""
    return json.loads(fetch(f"{AGY_RELEASES}/manifests/{os_name}_{arch}{suffix}.json"))


def latest_version(name):
    if name in PACKAGES:
        # Public publisher package metadata is useful for both native and npm.
        # Claude may intentionally follow stable; preserve that configured channel.
        package = PACKAGES[name].replace("/", "%2f")
        return parse_version(json.loads(fetch(f"https://registry.npmjs.org/{package}/latest"))["version"])
    if name == "grok":
        channel = os.environ.get("GROK_CHANNEL", "stable")
        if channel not in {"stable", "alpha", "enterprise"}:
            raise MaintenanceError("GROK_CHANNEL must be stable, alpha or enterprise")
        return parse_version(fetch(f"https://x.ai/cli/{channel}").decode())
    return parse_version(agy_manifest()["version"])


def update_agy(executable):
    """Use the vendor manifest/checksum; replace only a user-owned native file.

    Google's bootstrapper refuses to overwrite an existing binary and its install
    handoff changes shell configuration. A bounded verified payload replacement
    avoids deleting a working executable or rewriting the operator's profiles.
    """
    if executable.is_symlink() or executable.stat().st_uid != os.getuid():
        raise MaintenanceError("Agy update needs an ordinary native binary owned by your current user")
    if executable.parent.stat().st_uid != os.getuid() or not os.access(executable.parent, os.W_OK):
        raise MaintenanceError("Agy's installation directory must be writable and owned by your current user")
    manifest = agy_manifest()
    version = parse_version(manifest["version"])
    url, checksum = manifest["url"], manifest["sha512"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "storage.googleapis.com", "antigravity.google", urlparse(AGY_RELEASES).hostname,
    } or parsed.username or parsed.password:
        raise MaintenanceError("Agy release URL is outside the official HTTPS download hosts")
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{128}", checksum):
        raise MaintenanceError("Agy release SHA512 is invalid")
    maximum = 512 * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix=".agy-update-", dir=executable.parent) as temporary:
        payload, binary = Path(temporary) / "payload", Path(temporary) / "agy"
        digest, size = hashlib.sha512(), 0
        with urlopen(Request(url, headers={"User-Agent": "agentscient-harness-check"}), timeout=30) as response:
            if urlparse(response.url).scheme != "https":
                raise MaintenanceError("Agy download redirected outside HTTPS")
            with payload.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise MaintenanceError("Agy release exceeds the download limit")
                    digest.update(chunk)
                    stream.write(chunk)
        if digest.hexdigest() != checksum.lower():
            raise MaintenanceError("Agy release checksum mismatch; installed binary preserved")
        if parsed.path.endswith(".tar.gz"):
            with tarfile.open(payload, "r:gz") as archive:
                member = archive.getmember("antigravity")
                if not member.isfile() or member.size > maximum:
                    raise MaintenanceError("Agy archive does not contain a bounded ordinary executable")
                with archive.extractfile(member) as source, binary.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
        else:
            payload.rename(binary)
        binary.chmod(0o755)
        if installed_version("agy", binary) != version:
            raise MaintenanceError("Staged Agy version does not match its release; installed binary preserved")
        os.replace(binary, executable)


def install_native(name):
    if not shutil.which("curl") and not shutil.which("wget"):
        raise MaintenanceError("The vendor installer needs curl or wget; run dependencies.sh first")
    content = fetch(INSTALLERS[name])
    if not content.startswith(b"#!") or b"\x00" in content:
        raise MaintenanceError("Official installer did not return a shell script")
    with tempfile.TemporaryDirectory(prefix=f"agentscient-{name}-installer-") as temporary:
        installer = Path(temporary) / "install.sh"
        installer.write_bytes(content)
        installer.chmod(0o600)
        run(["sh" if name == "codex" else "bash", installer], timeout=600)


def update(name, manager, executable, package_dir, current, latest):
    if manager == "missing":
        install_native(name)
    elif manager == "npm":
        if not os.access(package_dir, os.W_OK) or not os.access(package_dir.parent, os.W_OK):
            raise MaintenanceError("npm's installation is not user-writable; use its original administrator or a user-owned npm prefix")
        run(["npm", "install", "--global", f"{PACKAGES[name]}@latest"], timeout=600)
    elif manager == "native":
        if name == "grok":
            run([executable, "update"], timeout=600)
        elif name == "claude":
            run([executable, "update"], timeout=600)
        elif name == "codex":
            install_native(name)
        else:
            if current != latest or not latest:
                update_agy(executable)
    elif manager == "homebrew":
        if not shutil.which("brew"):
            raise MaintenanceError("Homebrew owns this installation but brew is not on PATH")
        run(["brew", "upgrade", "--cask", package_dir], timeout=600)
    else:
        raise MaintenanceError("Installation owner is unknown (possibly apt, Windows or a custom shim); update through its original manager")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("check", "update"), nargs="?", default="check")
    parser.add_argument("--only", choices=CLIENTS, action="append", help="Limit to named clients; repeatable")
    parser.add_argument("--online", action="store_true", help="Compare versions with public vendor/package release metadata")
    parser.add_argument("--apply", action="store_true", help="Install missing clients and apply supported updates; update only")
    options = parser.parse_args(argv)
    if options.apply and options.command != "update":
        parser.error("--apply requires update")
    if options.apply and os.geteuid() == 0:
        print("BLOCKED: run harness maintenance as your normal user, without sudo; preserve that user's installations.")
        return 1
    names = list(dict.fromkeys(options.only or CLIENTS))
    print("No model requests or login changes. Agy is a local client; managed adapters remain Codex, Claude and Grok.")
    if not options.online and not options.apply:
        print("Offline inventory: freshness is unknown. Add --online to compare release versions.")
    failed = False
    for name in names:
        try:
            paths = executable_paths(name)
            manager, package_dir = installation(name, paths)
            executable = paths[0] if paths else None
            current = installed_version(name, executable) if executable else None
            latest = latest_version(name) if options.online or options.apply else None
            print(f"{name}: installed={current or 'missing'} manager={manager} latest={latest or 'not checked'}")
            if executable:
                print(f"  executable: {shlex.quote(str(executable))}")
            if name == "claude" and latest and current != latest:
                print("  Latest-channel comparison; claude update respects your configured release channel/policy.")
            if options.command == "update":
                if not options.apply:
                    print(f"  Preview: preserve {manager} ownership; rerun update --apply to make changes.")
                    if manager == "missing":
                        print(f"  Official installer: {INSTALLERS[name]} (may update shell startup PATH)")
                    continue
                print("  Applying vendor update/install (native installers may update shell startup PATH)...", flush=True)
                update(name, manager, executable, package_dir, current, latest)
                paths = executable_paths(name)
                if len(paths) != 1:
                    raise MaintenanceError("Update did not leave one discoverable executable; inspect your PATH")
                executable = paths[0]
                current = installed_version(name, executable)
                print(f"  Verified installed version: {current}")
            if not executable:
                failed = True
            elif shutil.which(name) is None:
                print(f"  BLOCKED: installed outside PATH. Add {executable.parent} to PATH and open a new terminal.")
                failed = True
            if latest and current != latest:
                print("  ATTENTION: installed version differs from published release; freshness is not verified.")
                failed = True
        except (MaintenanceError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
            # Parsing exceptions may include external response contents; print
            # only our controlled messages, never arbitrary provider output.
            detail = str(exc) if isinstance(exc, MaintenanceError) else "Invalid local installation or public release metadata"
            print(f"{name}: BLOCKED: {detail}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
