"""Manual Linux prerequisites; keep pinned executables separate from the host."""

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("python3", "python3-venv", "git", "gh", "tmux", "curl", "unzip", "ca-certificates", "tar", "coreutils")
SERENA = "agentscient-serena:v2-beta"


def command(argv, *, env=None, cwd=None, interactive=False, timeout=60):
    """Only interactive package installation inherits terminal output/password input."""
    try:
        return subprocess.run(argv, cwd=cwd, env=dict(env if env is not None else os.environ, LC_ALL="C"), text=True,
                              capture_output=not interactive, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Could not complete {Path(argv[0]).name}; check installation and connectivity.") from error


def required(argv, **kwargs):
    result = command(argv, **kwargs)
    if result.returncode:
        # Do not reproduce arbitrary tool stderr, URLs or authentication values.
        raise ValueError(f"{Path(argv[0]).name} failed (exit {result.returncode}); inspect that tool locally.")
    return result.stdout


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def manifest():
    value = json.loads((ROOT / ".github/actions/php-runtime/toolchain.json").read_text())
    if not re.fullmatch(r"[\w.-]+", value["tag"]):
        raise ValueError("Invalid owned toolchain tag.")
    for name in ("php", "composer"):
        if not re.fullmatch(r"[0-9a-f]{64}", value[name]["sha256"]):
            raise ValueError("Invalid owned toolchain digest.")
    return value


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError(f"Expected a private, owned directory: {path}")


def toolchain_check(destination, config):
    """Never repair or replace a published path: existing runs may be using it."""
    try:
        if destination.is_symlink():
            return False
        record = json.loads((destination / "receipt.json").read_text())
        if record["manifest"] != config:
            return False
        for name in ("php", "composer", "composer.phar"):
            path = destination / name
            if path.is_symlink() or not path.is_file() or digest(path) != record["files"][name]:
                return False
        return os.access(destination / "php", os.X_OK) and os.access(destination / "composer", os.X_OK)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def toolchain_install(state, config):
    private_directory(state)
    destination = state / config["tag"]
    if destination.exists() or destination.is_symlink():
        if not toolchain_check(destination, config):
            raise ValueError(f"Existing toolchain failed integrity checks: {destination}. Preserve it and select a new --toolchains directory.")
        print("PASS owned toolchain already installed; pinned files preserved.")
        return destination
    cache = state / "cache"
    private_directory(cache)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=state) as temporary:
        stage = Path(temporary)
        path_file = stage / "github-path"
        path_file.touch(mode=0o600)
        env = dict(os.environ, RUNNER_OS="Linux", RUNNER_ARCH="X64", RUNNER_TOOL_CACHE=str(cache),
                   RUNNER_TEMP=str(stage), GITHUB_PATH=str(path_file))
        if not env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
            result = command(["gh", "auth", "token", "--hostname", "github.com"])
            if result.returncode or not result.stdout.strip():
                raise ValueError("Owned toolchain download requires GitHub access; run gh auth login first.")
            env["GH_TOKEN"] = result.stdout.strip()
        elif not env.get("GH_TOKEN"):
            env["GH_TOKEN"] = env["GITHUB_TOKEN"]
        required([sys.executable, "-I", "-B", str(ROOT / ".github/actions/php-runtime/toolchain.py"), "install"],
                 env=env, timeout=600)
        entries = path_file.read_text().splitlines()
        if len(entries) != 1:
            raise ValueError("Owned installer did not return one runtime directory.")
        extracted = Path(entries[0]).resolve()
        if extracted.parent != stage.resolve():
            raise ValueError("Owned installer returned a directory outside staging.")
        receipt = {"manifest": config, "files": {name: digest(extracted / name)
                                                  for name in ("php", "composer", "composer.phar")}}
        (extracted / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        # rename refuses an existing populated directory, including a concurrent install.
        try:
            extracted.rename(destination)
        except OSError:
            if not destination.is_dir() or not toolchain_check(destination, config):
                raise ValueError("Concurrent toolchain installation failed verification.") from None
    print("PASS installed the repository's checksum-verified owned PHP and Composer release.")
    return destination


def apt_install():
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        release = {}
    family = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
    if not family.intersection({"debian", "ubuntu"}) or not shutil.which("apt-get"):
        raise ValueError("Automatic package installation supports Debian/Ubuntu only; install the listed prerequisites manually.")
    prefix = [] if os.geteuid() == 0 else ["sudo"]
    if prefix and not shutil.which("sudo"):
        raise ValueError("sudo is missing; install prerequisites through your administrator.")
    print("Refreshing package metadata and installing/updating only: " + ", ".join(PACKAGES), flush=True)
    required([*prefix, "apt-get", "update"], interactive=True, timeout=600)
    required([*prefix, "apt-get", "install", "--no-install-recommends", "-y", *PACKAGES],
             interactive=True, timeout=1800)


def packages_check():
    good = True
    print("Package candidates below use local APT metadata; install/update refreshes it first.")
    for package in PACKAGES:
        if not shutil.which("dpkg-query") or not shutil.which("apt-cache"):
            print("FAIL APT package inventory unavailable; verify named prerequisites using your distribution.")
            return False
        installed = command(["dpkg-query", "-W", "-f=${Status}\t${Version}", package])
        fields = installed.stdout.strip().split("\t")
        present = installed.returncode == 0 and len(fields) == 2 and fields[0] == "install ok installed"
        candidate_result = command(["apt-cache", "policy", package])
        match = re.search(r"^\s*Candidate:\s*(\S+)", candidate_result.stdout, re.MULTILINE)
        candidate = match.group(1) if match else "unknown"
        version = fields[-1] if present else "missing"
        known = candidate_result.returncode == 0 and candidate not in {"unknown", "(none)"}
        comparison = command(["dpkg", "--compare-versions", version, "lt", candidate]) if present and known else None
        verified = comparison is not None and comparison.returncode in {0, 1}
        outdated = verified and comparison.returncode == 0
        status = "NOT VERIFIED" if present and not verified else "FAIL" if not present or outdated else "PASS"
        good = good and present and verified and not outdated
        print(f"{status} {package}: installed={version}, candidate={candidate}")
    return good


def docker_check(*, build):
    if not shutil.which("docker"):
        print("FAIL Docker command is unavailable.")
        available = False
    else:
        info = command(["docker", "info", "--format", "{{.ServerVersion}} {{.Name}}"])
        available = info.returncode == 0
        print("PASS Docker daemon reachable." if available else "FAIL Docker daemon is unreachable.")
    if "microsoft" in platform.release().lower() or os.environ.get("WSL_DISTRO_NAME"):
        print("Docker Desktop/WSL: update/start Docker Desktop on Windows and enable Settings > Resources > WSL Integration for this distribution.")
    else:
        print("Docker installation/upgrades are managed separately: https://docs.docker.com/engine/install/")
    if not available:
        return False
    source = (ROOT / "devtools/serena/Dockerfile").read_text()
    match = re.search(r"^ARG SERENA_REVISION=([0-9a-f]{40})$", source, re.MULTILINE)
    if not match:
        raise ValueError("Cannot determine pinned Serena revision.")
    if build:
        print("Building the configured Serena image from the repository's Dockerfile.", flush=True)
        required(["docker", "build", "--tag", SERENA, str(ROOT / "devtools/serena")], timeout=1800)
    result = command(["docker", "image", "inspect", "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}', SERENA])
    valid = result.returncode == 0 and result.stdout.strip() == match.group(1)
    print("PASS Serena image source revision matches." if valid else "FAIL Serena image missing/stale; add --build-serena to install/update.")
    return valid


def locked_packages_match(project):
    """Autoload.php alone survives stale/missing packages; compare the retained inventory."""
    try:
        lock = json.loads((project / "composer.lock").read_text())
        installed = json.loads((project / "vendor/composer/installed.json").read_text())
        installed = installed["packages"] if isinstance(installed, dict) else installed
        expected = {item["name"]: item for item in lock.get("packages", []) + lock.get("packages-dev", [])}
        actual = {item["name"]: item for item in installed}
        if expected.keys() != actual.keys():
            return False
        for name, item in expected.items():
            if item["version"] != actual[name]["version"]:
                return False
            for source in ("source", "dist"):
                reference = item.get(source, {}).get("reference")
                if reference is not None and reference != actual[name].get(source, {}).get("reference"):
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def project_check(project, toolchain, *, install):
    project = project.expanduser().resolve()
    if not (project / "composer.json").is_file() or not (project / "composer.lock").is_file():
        raise ValueError(f"A Composer project and committed lockfile are required: {project}")
    before = (project / "composer.lock").read_bytes()
    composer = str(toolchain / "composer")
    env = dict(os.environ, PATH=str(toolchain) + os.pathsep + os.environ.get("PATH", ""))
    # Keep normal credential storage; discard switches that redirect the selected
    # project/vendor tree or hide failed PHP/dev requirements from this check.
    for key in ("COMPOSER", "COMPOSER_VENDOR_DIR", "COMPOSER_NO_DEV", "COMPOSER_IGNORE_PLATFORM_REQS", "COMPOSER_IGNORE_PLATFORM_REQ"):
        env.pop(key, None)
    if install:
        print(f"Installing locked Composer dependencies (including configured scripts/plugins): {project}", flush=True)
        print("Command: " + shlex.join([composer, "--working-dir", str(project), "install", "--prefer-dist", "--no-interaction"]))
        result = command([composer, "install", "--prefer-dist", "--no-interaction"], env=env, cwd=project, timeout=1800)
        if (project / "composer.lock").read_bytes() != before:
            raise ValueError("Composer lockfile changed during installation; inspect it before continuing.")
        if result.returncode:
            raise ValueError("Locked Composer installation failed; run the printed project command locally to inspect its diagnostics.")
    check_env = dict(env, COMPOSER_DISABLE_NETWORK="1")
    validation = command([composer, "validate", "--check-lock", "--no-check-publish", "--no-plugins", "--no-scripts", "--no-interaction"], cwd=project, env=check_env)
    result = command([composer, "check-platform-reqs", "--lock", "--no-plugins", "--no-scripts", "--no-interaction"], cwd=project, env=check_env)
    good = validation.returncode == 0 and result.returncode == 0 and (project / "vendor/autoload.php").is_file() and locked_packages_match(project)
    print(f"{'PASS' if good else 'FAIL'} current lockfile, PHP platform and installed package versions/references: {project}")
    return good


def parser():
    result = argparse.ArgumentParser(description=__doc__, epilog="No PHP/APT substitutions, global PATH edits, Composer updates, Docker replacement, or full host upgrades. sudo asks in your terminal; never put its password in an argument.")
    result.add_argument("action", nargs="?", choices=("check", "preview", "install", "update"), default="check")
    result.add_argument("--toolchains", type=Path, default=Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "agentscient/toolchains",
                        help="Private persistent toolchain directory (not a temporary cache).")
    result.add_argument("--project", type=Path, action="append", default=[], help="Check/install locked Composer dependencies for this project; repeatable.")
    result.add_argument("--build-serena", action="store_true", help="With install/update, build the repository's pinned Serena image.")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    mutate = args.action in {"install", "update"}
    if args.build_serena and not mutate:
        raise ValueError("--build-serena requires install or update.")
    config = manifest()
    state = args.toolchains.expanduser().absolute()
    destination = state / config["tag"]
    print(f"Owned release: {config['tag']}\nPersistent toolchain: {destination}")
    print("Acceptance options: " + shlex.join(["--php", str(destination / "php"), "--composer", str(destination / "composer")]))
    if args.action == "preview":
        print("Read-only check: cached APT candidates, pinned toolchain integrity, Docker and pinned Serena image.")
        print("Install/update: refresh APT metadata; install/update only " + ", ".join(PACKAGES) + "; install the owned release if absent.")
        print("Optional --project PATH installs from composer.lock; --build-serena builds the pinned provider.")
        return 0
    if sys.version_info < (3, 11) or platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ValueError("This setup requires Linux/X64 and Python 3.11+; use a supported WSL2 distribution.")
    if mutate:
        if os.geteuid() == 0:
            raise ValueError("Run this script as your normal user. Only its APT commands use sudo; toolchains and Composer stay user-owned.")
        apt_install()
        toolchain_install(state, config)
    good = packages_check()
    installed = toolchain_check(destination, config)
    print("PASS owned toolchain integrity." if installed else "FAIL owned toolchain missing/changed; use install with a fresh private destination.")
    good = installed and good
    good = docker_check(build=args.build_serena) and good
    for project in args.project:
        if installed:
            good = project_check(project, destination, install=mutate) and good
        else:
            print(f"FAIL cannot check Composer project without the owned toolchain: {project}")
            good = False
    print("Harness versions/authentication: run harnesses.sh; complete configured checks with acceptance.sh check.")
    return 0 if good else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Dependencies: {error}", file=sys.stderr)
        raise SystemExit(1) from None
