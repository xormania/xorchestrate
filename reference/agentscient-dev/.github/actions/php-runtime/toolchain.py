"""Retain and install the pinned CI executables; Python stdlib only."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.github.com"


def request(url, *, method="GET", payload=None, binary=False):
    """Attach credentials only to GitHub APIs, never to redirected storage URLs."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("Toolchain transfers require HTTPS")
    headers = {"Accept": "application/octet-stream" if binary else "application/vnd.github+json"}
    data = payload
    if isinstance(payload, dict):
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif payload is not None:
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    token = os.environ.get("GH_TOKEN")
    if token and parsed.hostname in {"api.github.com", "uploads.github.com"}:
        # urllib does not copy unredirected headers into a redirect request.
        req.add_unredirected_header("Authorization", "Bearer " + token)
    return urllib.request.urlopen(req, timeout=120)


def api(url, **kwargs):
    with request(url, **kwargs) as response:
        return json.load(response)


def manifest():
    config = json.loads((HERE / "toolchain.json").read_text())
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", config["repository"]):
        raise ValueError("Invalid toolchain repository")
    for component in ("php", "composer"):
        item = config[component]
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("Expected a pinned SHA-256 digest")
        if not re.fullmatch(r"[\w.-]+", item["asset"]):
            raise ValueError("Invalid asset filename")
    return config


def verified(path, digest):
    if not path.is_file():
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def download(url, path, digest):
    """Keep cache publication atomic; a bad transfer never becomes executable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if verified(path, digest):
        print(f"{path.name}: verified cache hit", flush=True)
        return
    print(f"{path.name}: cache miss", flush=True)
    fd, name = tempfile.mkstemp(prefix=".download.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as destination, request(url, binary=True) as response:
            shutil.copyfileobj(response, destination)
        if not verified(temporary, digest):
            raise ValueError(f"Checksum mismatch for {path.name}; pin unchanged")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cache_paths(config, root):
    # Preserve the existing PHP cache so the approved archive can be recovered.
    return {
        "php": root / "agentscient-php" / (config["php"]["sha256"] + ".tar.gz"),
        "composer": root / "agentscient-composer" / (config["composer"]["sha256"] + ".phar"),
    }


def release_url(config):
    return f"{API}/repos/{config['repository']}/releases"


def get_release(config):
    return api(release_url(config) + "/tags/" + urllib.parse.quote(config["tag"], safe=""))


def get_draft(config):
    # The tag endpoint promises published releases. Authenticated listing also
    # exposes drafts, including interrupted uploads whose tag does not yet exist.
    page = 1
    while True:
        releases = api(release_url(config) + f"?per_page=100&page={page}")
        matches = [item for item in releases if item["tag_name"] == config["tag"]]
        if len(matches) > 1:
            raise ValueError("Multiple releases use the configured toolchain tag")
        if matches:
            return matches[0]
        if len(releases) < 100:
            return None
        page += 1


def asset_urls(config, release):
    assets = {item["name"]: item for item in release["assets"]}
    result = {}
    for component in ("php", "composer"):
        item = assets[config[component]["asset"]]
        if item["state"] != "uploaded":
            raise ValueError("Release asset upload is incomplete")
        # Construct the authenticated API URL locally; don't trust an asset's URL.
        result[component] = release_url(config) + "/assets/" + str(int(item["id"]))
    return result


def install(config, paths):
    if os.environ.get("RUNNER_OS") != "Linux" or os.environ.get("RUNNER_ARCH") != "X64":
        raise ValueError("The pinned PHP runtime requires Linux/X64")
    missing = [key for key, path in paths.items() if not verified(path, config[key]["sha256"])]
    if missing:
        release = get_release(config)
        if release["draft"]:
            raise ValueError("The toolchain release has not been published")
        urls = asset_urls(config, release)
        for key in missing:
            download(urls[key], paths[key], config[key]["sha256"])
    # Extraction rechecks the selected local archive before executing its PHP.
    subprocess.run(["bash", str(HERE / "install-php.sh"), str(paths["php"]), config["php"]["sha256"]], check=True)
    runtime = Path(Path(os.environ["GITHUB_PATH"]).read_text().splitlines()[-1])
    shutil.copyfile(paths["composer"], runtime / "composer.phar")
    wrapper = runtime / "composer"
    wrapper.write_text('#!/bin/sh\nexec "$(dirname "$0")/php" "$(dirname "$0")/composer.phar" "$@"\n')
    wrapper.chmod(0o755)
    # Force the pinned PHP even when a global composer or PHP is already installed.
    result = subprocess.check_output([str(runtime / "php"), "-r", "echo PHP_VERSION;"], text=True)
    if result != config["php"]["version"]:
        raise ValueError("PHP version does not match the manifest")
    version = subprocess.check_output([str(wrapper), "--version", "--no-ansi"], text=True)
    if not version.startswith("Composer version " + config["composer"]["version"] + " "):
        raise ValueError("Composer version does not match the manifest")
    print(version, end="")


def publish(config, paths):
    """Operator workflow only: never replace existing release assets or pins."""
    if os.environ.get("GITHUB_REPOSITORY") != config["repository"]:
        raise ValueError("Publish from the configured repository only")
    revision = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Publish requires an exact source revision")
    try:
        release = get_release(config)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = get_draft(config)
    if release is None:
        # Recover only the original approved PHP bytes. A changed upstream archive fails.
        for key in paths:
            download(config[key]["source"], paths[key], config[key]["sha256"])
        body = "Retained CI executables; not an Agentscient product release.\n\n"
        for key in paths:
            item = config[key]
            body += f"- {item['asset']}: SHA-256 `{item['sha256']}`; source {item['source']}\n"
        body += "\nPHP: https://www.php.net/license/ ; Composer: https://github.com/composer/composer/blob/main/LICENSE\n"
        release = api(release_url(config), method="POST", payload={
            "tag_name": config["tag"], "target_commitish": revision,
            "name": config["tag"], "body": body, "draft": True, "make_latest": "false",
        })
    if release["draft"]:
        existing = {item["name"] for item in release["assets"]}
        for key, path in paths.items():
            item = config[key]
            if item["asset"] not in existing:
                download(item["source"], path, item["sha256"])
                url = f"https://uploads.github.com/repos/{config['repository']}/releases/{int(release['id'])}/assets?"
                api(url + urllib.parse.urlencode({"name": item["asset"]}), method="POST", payload=path.read_bytes())
        release = api(release_url(config) + "/" + str(int(release["id"])))
    # Read back both uploaded assets into an empty cache before publication.
    urls = asset_urls(config, release)
    with tempfile.TemporaryDirectory(prefix="verify-toolchain-", dir=os.environ["RUNNER_TEMP"]) as name:
        for key in paths:
            download(urls[key], Path(name) / config[key]["asset"], config[key]["sha256"])
    if release["draft"]:
        release = api(release_url(config) + "/" + str(int(release["id"])), method="PATCH",
                      payload={"draft": False, "make_latest": "false"})
    print(f"Verified retained toolchain: {release['html_url']}; immutable={release.get('immutable', False)}")


def main():
    config = manifest()
    paths = cache_paths(config, Path(os.environ["RUNNER_TOOL_CACHE"]))
    if sys.argv[1:] == ["install"]:
        install(config, paths)
    elif sys.argv[1:] == ["publish"]:
        publish(config, paths)
    else:
        raise ValueError("Usage: toolchain.py install|publish")


if __name__ == "__main__":
    main()
