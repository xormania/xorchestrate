"""Isolated CI execution and source-validated reuse of original check receipts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from ops.devlane.workflow.checks import ci_runners, lane
from ops.testkit import Check, World, execute_checks, run

ROOT = lane.ROOT
SCHEMA = "ci-proof/2"
WORKFLOW = ci_runners.WORKFLOW
TOOLS = {
    "git": ("--version",), "python3": ("--version",), "bash": ("--version",),
    "ruff": ("--version",), "rg": ("--version",), "cue": ("version",),
    "jq": ("--version",), "tmux": ("-V",),
    "bwrap": ("--version",),
    "php": ("--version",), "composer": ("--version",),
}
LIMIT = 2 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def checked(argv, root, env):
    result = run(argv, cwd=root, env=env, timeout=60)
    if result.exit:
        raise ValueError(f"ci: expected successful {argv[0]}; found exit {result.exit}; needed {result.stderr.strip()}")
    return result.stdout.strip()


def git(root, env, *args):
    return checked(("git", *args), root, env)


def canonical_commit(root, env, source):
    """Same integration tree and ordered parents, independent of merge messages."""
    tree = git(root, env, "rev-parse", source + "^{tree}")
    parents = git(root, env, "show", "-s", "--format=%P", source).split()
    argv = ["git", "commit-tree", tree]
    for parent in parents:
        argv.extend(("-p", parent))
    argv.extend(("-m", "CI verification snapshot"))
    return checked(argv, root, env), tree, parents


def toolchain(root, env, kind):
    declared = ("git", "python3", "bash", "ruff", "rg", "cue", "jq")
    if kind == "local":
        declared += ("bwrap",)
    elif kind == "app":
        declared = ("git", "python3", "bash", "php", "composer", "jq")
    names = {k: TOOLS[k] for k in declared}
    found = {}
    for name, args in names.items():
        path = shutil.which(name, path=env["PATH"])
        if path is None:
            raise ValueError(f"ci: expected declared tool {name}; found absent; needed install it on the runner")
        version = checked((path, *args), root, env).splitlines()[:2]
        found[name] = {"version": version, "binary": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    if kind == "local":
        # Exercise the capability the existing profile tests require before
        # spending minutes launching fixtures which would all refuse it.
        checked(("bwrap", "--unshare-user", "--ro-bind", "/", "/", "--proc", "/proc",
                 "--dev", "/dev", "--", "/bin/true"), root, env)
    return {"tools": found, "system": platform.system(), "machine": platform.machine(),
            "libc": platform.libc_ver()}


def app_plan(root):
    scripts = (
        ("composer-validate", ("composer", "validate", "--strict")),
        ("composer-install", ("composer", "install", "--prefer-dist", "--no-progress", "--no-interaction")),
        ("php-lint", ("bash", "-euo", "pipefail", "-c", "find src packages -name '*.php' -print0 | xargs -0 -r -n50 php -l")),
        ("kernel", ("php", "bin/console", "about")),
        ("phpunit", ("vendor/bin/phpunit",)),
        ("mate", ("bash", "-euo", "pipefail", "-c", "vendor/bin/mate tools:call symfony-services --query=router --limit=10 --format=json | jq -e '.untrusted_data.services[\"router.default\"]'; vendor/bin/mate tools:list")),
    )
    return [Check(name, argv, 240, ("composer.json", "composer.lock")) for name, argv in scripts]


def application_digest(entries):
    # The founding lane boundary makes ops independent of the application.
    # The CI adapter/kit remain inputs even when the product tree is unchanged.
    return digest([line for line in sorted(entries) if not line.split("\t", 1)[1].startswith("ops/")
                   or line.split("\t", 1)[1].startswith(("ops/testkit/", "ops/devlane/workflow/"))])


def application_tree(root, env):
    return application_digest(git(root, env, "ls-tree", "-rz", "HEAD").rstrip("\0").split("\0"))


def seed_probe_store(root, env):
    """Materialize tracked probe fixtures in the private CI world, never the host."""
    from ops.conductor.tools import decisions, instrument, laneconf

    name = laneconf.parse_file(root / "ops/lane.conf")["LANE_NAME"]
    path = Path(env["XDG_STATE_HOME"]) / name / "decisions.db"
    if path.exists():
        raise ValueError("ci: expected an empty private decision fixture; found a store; needed a fresh job home")
    rows = json.loads((root / "ops/conductor/decisions.seed.json").read_text())
    with contextlib.closing(instrument.connect_store(path, mode="rwc")) as connection:
        connection.executescript(decisions.SCHEMA)
        columns = [row[1] for row in connection.execute("PRAGMA table_info(decisions)")]
        if not rows or any(set(row) != set(columns) for row in rows):
            raise ValueError("ci: expected the decision schema's tracked seed; found incompatible rows; needed a current export")
        # Identifiers come from the domain schema; all exported values bind as parameters.
        sql = f"INSERT INTO decisions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"  # noqa: S608 -- identifiers from domain schema
        connection.executemany(sql, [tuple(row[column] for column in columns) for row in rows])
        connection.commit()
    checked((sys.executable, "ops/conductor/tools/skills.py", "sync"), root, env)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("ci: expected owner/repository; found invalid repository; needed GITHUB_REPOSITORY")
        self.base = "https://api.github.com/repos/" + repository
        self.token = token
        self.deadline = time.monotonic() + 10

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Evidence lookup budget exhausted")
        return min(5, remaining)

    def request(self, path):
        return urllib.request.Request(self.base + path, headers={  # noqa: S310 -- fixed HTTPS API origin
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ci-proof",
        })

    def get(self, path):
        with urllib.request.urlopen(self.request(path), timeout=self.timeout()) as response:  # noqa: S310 -- fixed HTTPS API origin
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("GitHub response exceeds the receipt limit")
        return json.loads(raw)

    def receipt(self, artifact):
        # Artifact redirects go to blob storage. Never forward the API token.
        try:
            urllib.request.build_opener(NoRedirect).open(self.request(f"/actions/artifacts/{artifact['id']}/zip"), timeout=self.timeout())
        except urllib.error.HTTPError as exc:
            if exc.code != 302:
                raise
            location = exc.headers["Location"]
        else:
            raise ValueError("Artifact download did not provide a redirect")
        url = urllib.parse.urlparse(location)
        if url.scheme != "https" or not url.hostname or not url.hostname.endswith((".blob.core.windows.net", ".githubusercontent.com", ".github.com")):
            raise ValueError("Unrecognized artifact download host")
        with urllib.request.urlopen(location, timeout=self.timeout()) as response:  # noqa: S310 -- HTTPS storage host checked above
            blob = response.read(LIMIT + 1)
        if len(blob) > LIMIT:
            raise ValueError("Artifact exceeds the receipt limit")
        if artifact.get("digest") != "sha256:" + hashlib.sha256(blob).hexdigest():
            raise ValueError("Artifact integrity is unverified")
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "receipt.json" or entries[0].file_size > LIMIT:
                raise ValueError("Artifact is not one bounded receipt")
            return json.loads(archive.read(entries[0]))


def reusable(api, *, key, checks, kind, repository, current_run, current_attempt=1):
    """Read original executions only; unavailable/unverifiable evidence is a miss."""
    names = {c.name for c in checks}
    prefix = f"ci-proof-{kind}-{key}-"
    cached = {}
    for page in range(1, 4):
        artifacts = api.get(f"/actions/artifacts?per_page=100&page={page}").get("artifacts", [])
        for artifact in artifacts:
            if artifact.get("expired") or not artifact.get("name", "").startswith(prefix):
                continue
            source_run = artifact.get("workflow_run", {}).get("id")
            if not isinstance(source_run, int):
                continue
            receipt = api.receipt(artifact)
            if (receipt.get("schema") != SCHEMA or receipt.get("key") != key
                    or receipt.get("repository") != repository or receipt.get("kind") != kind
                    or receipt.get("run_id") != source_run or receipt.get("plan") != sorted(names)
                    or digest(receipt.get("inputs")) != key):
                continue
            attempt = receipt.get("attempt")
            if (not isinstance(attempt, int) or attempt < 1
                    or (source_run == current_run and attempt >= current_attempt)):
                continue
            source = api.get(f"/actions/runs/{source_run}/attempts/{attempt}")
            if (source.get("path") != WORKFLOW or source.get("status") != "completed"
                    or source.get("conclusion") not in ("success", "failure")
                    or source.get("head_sha") != receipt.get("source_sha")
                    or source.get("repository", {}).get("full_name") != repository
                    or source.get("head_repository", {}).get("full_name") != repository
                    or source.get("event") not in ("pull_request", "push", "merge_group", "workflow_dispatch")):
                continue
            revision = receipt.get("event_revision", "")
            if not isinstance(revision, str) or not SHA.fullmatch(revision):
                continue
            commit = api.get(f"/git/commits/{revision}")
            parents = [p.get("sha") for p in commit.get("parents", [])]
            if commit.get("tree", {}).get("sha") != receipt.get("tree") or parents != receipt.get("parents"):
                continue
            if source["event"] == "pull_request":
                if len(parents) != 2 or parents[-1] != source["head_sha"]:
                    continue
            elif revision != source["head_sha"]:
                continue
            if kind != "app":
                if (receipt["inputs"].get("tree") != receipt["tree"]
                        or receipt.get("tested_revision") != receipt["inputs"].get("git", {}).get("canonical")):
                    continue
            else:
                tree = api.get(f"/git/trees/{receipt['tree']}?recursive=1")
                if tree.get("truncated") is not False:
                    continue
                entries = [f"{e['mode']} {e['type']} {e['sha']}\t{e['path']}" for e in tree["tree"]
                           if e["type"] in ("blob", "commit")]
                if application_digest(entries) != receipt["inputs"].get("tree"):
                    continue
            jobs = api.get(f"/actions/runs/{source_run}/attempts/{attempt}/jobs?per_page=100").get("jobs", [])
            # New runs separate execution from the final `lane` status. Keep
            # reading historical receipts, but never attribute one to the gate.
            execution_job = "dev-lane" if kind == "lane" and any(
                job.get("name") == "dev-lane" for job in jobs) else kind
            jobs = [job for job in jobs if job.get("name") == execution_job and job.get("status") == "completed"
                    and job.get("conclusion") in ("success", "failure")]
            if len(jobs) != 1:
                continue
            rows = receipt.get("results", [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                continue
            row_names = [row.get("name") for row in rows]
            if len(set(row_names)) != len(row_names) or not set(row_names) <= names:
                continue
            for row in rows:
                name = row.get("name")
                if name in names and name not in cached and row.get("mode") == "executed" and type(row.get("exit")) is int and row["exit"] == 0:
                    cached[name] = {"url": jobs[0]["html_url"], "run_id": source_run,
                                    "attempt": attempt, "seconds": row.get("seconds", 0)}
            if set(cached) == names:
                return cached
        if len(artifacts) < 100:
            break
    return cached


def write_output(path, **values):
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.writelines(f"{name}={value}\n" for name, value in values.items())


def run_ci(kind, *, root=ROOT, ambient=None, api=None):
    ambient = dict(os.environ) if ambient is None else ambient
    runner = ci_runners.runner_info(ambient)
    if kind not in ("lane", "app"):
        raise ValueError("ci: unknown check kind")
    profile = ("local" if runner["environment"] in ("direct", "self-hosted") else "ci") if kind == "lane" else "app"
    # Interpreter-heavy CLI suites contend badly beyond available CPUs.
    workers = min(8, os.cpu_count() or 1) if kind != "app" else 1
    started = time.monotonic()
    repository = ambient.get("GITHUB_REPOSITORY", "")
    run_id, attempt = int(ambient.get("GITHUB_RUN_ID", "0")), int(ambient.get("GITHUB_RUN_ATTEMPT", "1"))
    receipt_dir = Path(tempfile.mkdtemp(prefix=f"ci-proof-{kind}-{run_id}-{attempt}-",
                                     dir=ambient.get("RUNNER_TEMP")))
    receipt_path = receipt_dir / "receipt.json"
    # Preserve only declared executable access. No operator config or API token
    # is in the environment passed to tests, Composer, or child instruments.
    path = os.pathsep.join((str(Path(ambient.get("HOME", "")) / ".local/bin"), ambient.get("PATH", "")))
    with World(host_path=False) as world:
        env = world.env({"PATH": path, "TERM_WALL": ambient.get("TERM_WALL", "")})
        if kind == "app":
            downloads = Path(ambient.get("RUNNER_TOOL_CACHE", env["XDG_CACHE_HOME"])) / "ci-composer-downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            env["COMPOSER_CACHE_DIR"] = str(downloads)
        tools = toolchain(root, env, profile)
        if git(root, env, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("ci: expected a clean checked-out source tree; found local changes; needed a clean CI checkout")
        source = git(root, env, "rev-parse", "HEAD")
        canonical, tree, parents = canonical_commit(root, env, source)
        refs = git(root, env, "for-each-ref", "--format=%(refname) %(objectname)",
                   "refs/heads/records/", "refs/remotes/origin/records/")
        checks = lane.plan(root, profile=profile) if kind != "app" else app_plan(root)
        if kind != "app" and any(c.name == "probes" for c in checks):
            seed_probe_store(root, env)
        # Probes can read mutable forge state and publication metadata. Always
        # execute this cheap live observation on the actual event checkout.
        reusable_checks = [c for c in checks if c.name != "probes"]
        inputs = {"schema": SCHEMA, "tree": tree if kind != "app" else application_tree(root, env),
                  "git": {"canonical": canonical, "records": refs} if kind != "app" else None,
                  "toolchain": tools, "runner_environment": runner,
                  "workers": workers, "profile": profile,
                  "term_wall": digest(env["TERM_WALL"]) if kind != "app" else None,
                  "checks": [(c.name, c.argv, c.timeout) for c in checks]}
        key = digest(inputs)
        source_sha = ambient.get("GITHUB_SHA", source)
        if ambient.get("GITHUB_EVENT_PATH"):
            event = json.loads(Path(ambient["GITHUB_EVENT_PATH"]).read_text())
            source_sha = event.get("pull_request", {}).get("head", {}).get("sha", source_sha)
        receipt = {"schema": SCHEMA, "key": key, "repository": repository, "kind": kind, "profile": profile,
                   "run_id": run_id, "attempt": attempt, "source_sha": source_sha,
                   "event_revision": source, "tested_revision": canonical, "tree": tree,
                   "parents": parents, "inputs": inputs, "plan": sorted(c.name for c in checks),
                   "runner": ambient.get("RUNNER_NAME", "local"), "results": []}
        receipt["capacity"] = {"cpus": os.cpu_count(), "workers": workers}
        cached = {}
        if api is None and ambient.get("GITHUB_TOKEN") and repository:
            api = GitHub(repository, ambient["GITHUB_TOKEN"])
        if api:
            try:
                cached = reusable(api, key=key, checks=checks, kind=kind,
                                  repository=repository, current_run=run_id, current_attempt=attempt)
                cached = {name: row for name, row in cached.items() if name in {c.name for c in reusable_checks}}
            except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                print(f"Reuse unavailable ({reason}); executing required checks.", flush=True)
        if kind == "app" and set(cached) != {c.name for c in checks}:
            # Installing dependencies produces local files. A previous install
            # cannot supply vendor/ to a fresh job that must execute a check.
            cached.pop("composer-install", None)
        print(f"CI {kind} profile={profile}: source={source} tested={canonical} inputs={key} runner={receipt['runner']} environment={json.dumps(runner, sort_keys=True)}", flush=True)
        print(f"CI capacity: {json.dumps(receipt['capacity'], sort_keys=True)}", flush=True)

        def record(name, result, origin):
            row = {"name": name, "mode": "reused" if origin else "executed",
                   "exit": 0 if origin else result.exit,
                   "seconds": 0 if origin else round(result.duration, 3)}
            if origin:
                row["origin"] = origin
            receipt["results"].append(row)
            receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")

        # Live guards see the original checkout. Deterministic suites get the
        # exact same canonical object before and after publication by merge.
        guard_names = {c.name for c in lane.GUARDS} if kind != "app" else set()
        guards = [c for c in checks if c.name in guard_names]
        tests = [c for c in checks if c.name not in guard_names]
        execute_checks(checks, cwd=root, env=env)  # complete preflight, no starts
        status = lane.run_plan(guards, root=root, env=env, scope="GUARDS", cached=cached, record=record) if guards else 0
        if status == 0:
            git(root, env, "checkout", "--detach", canonical)
            try:
                status = lane.run_plan(tests, root=root, env=env, scope="SUITES" if kind != "app" else "APP",
                                       cached=cached, record=record, workers=workers)
            finally:
                git(root, env, "checkout", "--detach", source)
        receipt["exit"] = status
        receipt["seconds"] = round(time.monotonic() - started, 3)
        receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        write_output(ambient.get("GITHUB_OUTPUT"), receipt=str(receipt_path),
                     artifact=f"ci-proof-{kind}-{key}-{run_id}-{attempt}")
        summary = ambient.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as out:
                out.write(f"### {kind} / {profile}: {'PASS' if status == 0 else 'FAIL'} ({receipt['seconds']}s)\n\n")
                out.write(f"Runner: `{receipt['runner']}`. Source: `{source}`. Tested: `{canonical}`.\n\n")
                out.write(f"Environment: `{json.dumps(runner, sort_keys=True)}`.\n\n")
                out.write("| Check | Result | Execution | Seconds / original proof |\n|---|---|---|---|\n")
                for row in receipt["results"]:
                    proof = row.get("origin", {}).get("url", str(row["seconds"]))
                    out.write(f"| {row['name']} | {'PASS' if row['exit'] == 0 else 'FAIL'} | {row['mode']} | {proof} |\n")
        print(f"{profile.upper()} {kind}: {'PASS' if status == 0 else 'FAIL'} ({len(receipt['results'])}/{len(checks)} accounted)", flush=True)
        return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("lane", "app"))
    args = parser.parse_args(argv)
    try:
        return run_ci(args.kind)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
