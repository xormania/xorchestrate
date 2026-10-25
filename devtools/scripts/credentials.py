"""Credential inventory and explicit read-only API probes, with redacted output.

Official contracts checked 2026-10-02:
https://cli.github.com/manual/gh_auth_status
https://github.blog/changelog/2021-07-26-expiration-options-for-personal-access-tokens/
https://developers.openai.com/codex/auth/
https://platform.openai.com/docs/api-reference/models/list
https://code.claude.com/docs/en/cli-reference
https://platform.claude.com/docs/en/api/models/list
https://docs.x.ai/developers/rest-api-reference/inference/other
https://antigravity.google/docs/cli/install/
https://ai.google.dev/api/models

Subscription credentials have no common read-only authentication API. Native
status is reported separately from live API success. Jev --online makes a small
evaluation for each distinct configured connection/model/transport and can incur
provider charges. No login, refresh request or keychain export is issued here.
During --online, native status commands may manage their own credential cache.
"""

import argparse
import base64
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

# The shell wrapper uses isolated Python; only this script's checkout supplies
# runtime imports, regardless of the operator's cwd or PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SERVICES = ("github", "codex", "claude", "grok", "agy", "jev")
LIMIT = 256 * 1024
RENEW = {
    "github": "gh auth login --hostname github.com (or replace the selected environment token)",
    "codex": "codex login (or rotate the API key in its current source)",
    "claude": "claude auth login (or rotate the API key in its current source)",
    "grok": "grok login (or rotate XAI_API_KEY)",
    "agy": "open agy and sign in (or rotate GEMINI_API_KEY in Gemini provider mode)",
    "jev": "rotate the key in its configured file/environment source using https://console.typesafe.ai/keys",
}


class CheckError(Exception):
    """Only fixed, non-secret diagnostic text may be used here."""


def read_document(path, *, private=True, toml=False):
    """Do not follow a credential symlink or read an unbounded/special file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise CheckError("credential/config file is unreadable or a symlink") from exc
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise CheckError("credential/config path is not a regular file")
        if private and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
            raise CheckError("credential file must be owned by this user and private (chmod 600)")
        raw = handle.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise CheckError("credential/config file exceeds the size limit")
    try:
        value = tomllib.loads(raw.decode()) if toml else json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CheckError("credential/config file is malformed") from exc
    if not isinstance(value, dict):
        raise CheckError("credential/config file must contain an object")
    return value


def timestamp(value, *, milliseconds=False):
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" UTC", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return datetime.fromtimestamp(value / (1000 if milliseconds else 1), timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def jwt_expiry(token):
    # Metadata only: this does not validate the JWT signature, issuer or access.
    if not isinstance(token, str) or len(token) > 32768 or token.count(".") != 2:
        return None
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return timestamp(payload.get("exp")) if isinstance(payload, dict) else None
    except (ValueError, UnicodeError):
        return None


def expiry_report(value, now, days):
    if value is None:
        return {"expires_at": None, "expiry": "unknown; not published or inaccessible"}
    remaining = (value - now).total_seconds()
    return {"expires_at": value.isoformat().replace("+00:00", "Z"),
            "expiry": "expired" if remaining <= 0 else "soon" if remaining <= days * 86400 else "future"}


def native(argv):
    # Never print native stdout/stderr (it may include tokens/account identities).
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
           "DISABLE_AUTOUPDATER": "1", "NO_COLOR": "1"}
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=30, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError("native credential check unavailable or timed out") from exc
    if result.returncode:
        raise CheckError("native credential check failed; inspect login/connectivity locally")
    if len(result.stdout) > LIMIT:
        raise CheckError("native credential response exceeds the size limit")
    return result.stdout.decode("utf-8", "replace")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request_json(url, headers):
    # Only fixed official URLs reach this function. Never forward secrets on redirects.
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=20) as response:
            raw = response.read(LIMIT + 1)
    except HTTPError as exc:
        raise CheckError(f"read-only API probe returned HTTP {exc.code}") from None
    except (OSError, URLError, ValueError) as exc:
        raise CheckError("read-only API probe failed; check network/TLS and retry") from exc
    if len(raw) > LIMIT:
        raise CheckError("API response exceeds the size limit")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CheckError("API probe returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise CheckError("API probe returned an unexpected response")
    return value


def home_for(variable, suffix):
    return Path(os.environ.get(variable, str(Path.home() / suffix))).expanduser()


def key(value):
    return value if isinstance(value, str) and value.strip() else None


def inventory(name):
    """Return public metadata separately from a key kept only in this process."""
    row = {"service": name, "source": "unknown", "stored": "unknown", "native_status": "not checked",
           "live": "not checked", "renew": RENEW[name], "harness_access": "not verified; no model request made"}
    secret, expires = None, None
    if name == "github":
        source = next((name for name in ("GH_TOKEN", "GITHUB_TOKEN") if key(os.environ.get(name))), None)
        row.update(source=source or "GitHub CLI credential store", stored="present" if source else "unknown")
    elif name == "codex":
        root = home_for("CODEX_HOME", ".codex")
        config = read_document(root / "config.toml", private=False, toml=True)
        providers = config.get("model_providers", {})
        if not isinstance(providers, dict):
            raise CheckError("Codex model provider configuration is malformed")
        openai = providers.get("openai", {})
        if not isinstance(openai, dict):
            raise CheckError("Codex OpenAI provider configuration is malformed")
        storage = config.get("cli_auth_credentials_store", "file")
        if not isinstance(storage, str) or storage not in {"file", "keyring", "auto", "ephemeral"}:
            raise CheckError("Codex credential storage setting is invalid")
        if config.get("model_provider", "openai") != "openai" or openai:
            row.update(source="custom provider settings; use its credential checker", stored="unknown")
        elif storage in {"keyring", "auto", "ephemeral"}:
            row.update(source="Codex native credential store", stored="unknown")
        else:
            data = read_document(root / "auth.json")
            tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
            secret = key(data.get("OPENAI_API_KEY")) if data.get("auth_mode") != "chatgpt" else None
            access = key(tokens.get("access_token"))
            row.update(source=str(root / "auth.json"), stored="present" if secret or access else "missing")
            expires = jwt_expiry(access) if not secret else None
            # Environment keys alone do not establish which login Codex selected.
            if not data and key(os.environ.get("OPENAI_API_KEY")):
                row.update(source="OPENAI_API_KEY (native login selection unverified)", stored="present", selection_unknown=True)
                secret = os.environ["OPENAI_API_KEY"]
    elif name == "claude":
        if any(os.environ.get(flag) for flag in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
            row.update(source="external Claude provider", stored="unknown")
        elif key(os.environ.get("ANTHROPIC_API_KEY")):
            row["selection_unknown"] = True  # Native approval/provider selection is not inferred.
            secret = os.environ["ANTHROPIC_API_KEY"]
            row.update(source="ANTHROPIC_API_KEY", stored="present")
        elif key(os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            row.update(source="ANTHROPIC_AUTH_TOKEN", stored="present")
            expires = jwt_expiry(os.environ["ANTHROPIC_AUTH_TOKEN"])
        elif key(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
            row.update(source="CLAUDE_CODE_OAUTH_TOKEN", stored="present")
            expires = jwt_expiry(os.environ["CLAUDE_CODE_OAUTH_TOKEN"])
        else:
            path = home_for("CLAUDE_CONFIG_DIR", ".claude") / ".credentials.json"
            data = read_document(path)
            oauth = data.get("claudeAiOauth", {})
            if not isinstance(oauth, dict):
                raise CheckError("Claude OAuth credential shape is invalid")
            row.update(source=str(path) if data else "Claude native credential store",
                       stored="present" if key(oauth.get("accessToken")) else "unknown")
            expires = timestamp(oauth.get("expiresAt"), milliseconds=True)
    elif name == "grok":
        secret = key(os.environ.get("XAI_API_KEY"))
        if secret:
            row.update(source="XAI_API_KEY", stored="present")
        else:
            path = home_for("GROK_HOME", ".grok") / "auth.json"
            data = read_document(path)
            row.update(source=str(path), stored="file present; native format not validated" if data else "missing")
            expires = jwt_expiry(data.get("access_token"))
    else:
        path = Path.home() / ".gemini/antigravity-cli/settings.json"
        data = read_document(path, private=False)
        if data.get("modelProvider") == "gemini":
            secret = key(os.environ.get("GEMINI_API_KEY"))
            row.update(source="GEMINI_API_KEY (Agy Gemini provider)", stored="present" if secret else "missing")
        else:
            row.update(source="Agy operating-system keyring", stored="unknown")
    return row, secret, expires


def probe_key(name, secret):
    if "\n" in secret or "\r" in secret or len(secret) > 32768:
        raise CheckError("API key has an invalid format")
    if name == "codex":
        if os.environ.get("OPENAI_BASE_URL"):
            return "not verified: custom endpoint configured"
        data = request_json("https://api.openai.com/v1/models", {"Authorization": "Bearer " + secret})
    elif name == "claude":
        if os.environ.get("ANTHROPIC_BASE_URL"):
            return "not verified: custom endpoint configured"
        data = request_json("https://api.anthropic.com/v1/models?limit=1",
                            {"x-api-key": secret, "anthropic-version": "2023-06-01"})
    elif name == "grok":
        data = request_json("https://api.x.ai/v1/api-key", {"Authorization": "Bearer " + secret})
        for field in ("api_key_blocked", "api_key_disabled", "team_blocked"):
            if data.get(field) is not False:
                raise CheckError("xAI API key is blocked/disabled or status is unavailable")
        return "verified: API key active (model permissions not tested)"
    else:
        data = request_json("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                            {"x-goog-api-key": secret})
    if not isinstance(data.get("models" if name == "agy" else "data"), list):
        raise CheckError("model-list API returned an unexpected response")
    return "verified: authenticated model-list access (generation not tested)"


def github_online(row, repos):
    native(["gh", "auth", "status", "--active", "--hostname", "github.com"])
    row["native_status"] = "passed"
    output = native(["gh", "api", "--hostname", "github.com", "--include", "user"])
    header = re.search(r"(?im)^github-authentication-token-expiration:\s*([^\r\n]+)", output)
    # Validate API JSON without printing identity fields.
    parts = re.split(r"\r?\n\r?\n", output, maxsplit=1)
    try:
        identity = json.loads(parts[1]) if len(parts) == 2 else {}
    except ValueError as exc:
        raise CheckError("GitHub identity response was malformed") from exc
    if not isinstance(identity, dict) or not isinstance(identity.get("id"), int):
        raise CheckError("GitHub identity response was not authenticated")
    row["repositories"] = []
    for repo in repos:
        try:
            data = json.loads(native(["gh", "api", "--hostname", "github.com", "repos/" + repo]))
        except ValueError as exc:
            raise CheckError("GitHub repository response was malformed") from exc
        if (not isinstance(data, dict) or not isinstance(data.get("full_name"), str)
                or data["full_name"].lower() != repo.lower()):
            raise CheckError("GitHub repository response did not match")
        permissions = data.get("permissions")
        row["repositories"].append({"repository": repo, "read": True,
                                    "push": permissions.get("push") is True if isinstance(permissions, dict) else None})
    row.update(stored="present", live="verified: GitHub identity and requested repository reads")
    return timestamp(header.group(1)) if header else None


def jev_settings(configuration):
    """Use ordinary project/role resolution without creating a persistent team."""
    from devtools.configuration import resolve, selections
    from devtools.runtime import defaults as runtime_defaults
    from devtools.store import State

    configured = Path(configuration).expanduser().resolve() if configuration else None
    if configured is None:
        candidate = Path.home() / ".config/agentscient/configuration.json"
        if candidate.exists() or candidate.is_symlink():
            configured = candidate.resolve()
    if configured is not None and not configured.is_file():
        raise CheckError("Jev configuration file is missing or unreadable")
    with tempfile.TemporaryDirectory(prefix="agentscient-credential-check-") as temporary:
        directory = Path(temporary)
        project = {"configuration_file": str(configured)} if configured is not None else {}
        (directory / "team.json").write_text(json.dumps({"project": project, "runtime": True}))
        runtime_defaults(directory)
        state = State(directory)
        return [resolve(state, **selection) for selection in selections(state)]


def check_jev(*, online, configuration, now, days):
    """Check referenced keys and optionally evaluate through the installed adapter.

    Output is deliberately limited to fixed labels and counts. Configuration text,
    provider responses and exceptions can contain secrets and are never reported.
    """
    row = {"service": "jev", "source": "runtime configuration", "stored": "unknown",
           "native_status": "not applicable; no Jev CLI required", "live": "not checked",
           "renew": RENEW["jev"], "harness_access": "not applicable; evaluation API",
           "status": "incomplete", "checks": []}
    row.update(expiry_report(None, now, days))
    try:
        settings = jev_settings(configuration)
        from devtools.credentials import CredentialUnavailable, read_credential
        from devtools.jev import Jev
    except CheckError as exc:
        row.update(status="failed", error=str(exc))
        return row
    except (ValueError, OSError, RuntimeError, ImportError, TypeError):
        row.update(status="failed", error="Jev configuration could not be resolved; check the overlay and its project/role references")
        return row
    seen = set()
    for item in settings:
        identity = json.dumps([item[name] for name in ("connection", "model", "transport")], sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        reference = item["connection"]["credential"]
        result = {"source": "private credential file" if "file" in reference else "environment variable",
                  "stored": "unknown", "live": "not checked", "status": "incomplete"}
        row["checks"].append(result)
        try:
            read_credential(reference)
            result["stored"] = "present"
        except CredentialUnavailable:
            result.update(stored="missing", status="failed", error="configured Jev credential is missing")
            continue
        except (ValueError, OSError):
            result.update(status="failed", error="configured Jev credential is unreadable or invalid; check source and file permissions")
            continue
        if online:
            try:
                Jev(item["transport"], item["connection"]).evaluate(
                    {"value": 2}, {"check": {"type": "noul", "instructions": "Is value equal to 2?"}},
                    model=item["model"])
                result.update(status="verified", live="verified: authenticated Jev request and validated response")
            except Exception:
                # Installed evaluation adapters may use arbitrary exception types.
                # Never propagate their messages, chained errors or response bodies.
                result.update(status="failed", error="Jev request failed; check configured credential, endpoint/model and provider access")
    row["source"] = ", ".join(sorted({result["source"] for result in row["checks"]}))
    row["stored"] = "present" if all(result["stored"] == "present" for result in row["checks"]) else "incomplete"
    row["status"] = "failed" if any(result["status"] == "failed" for result in row["checks"]) else "verified" if online else "incomplete"
    row["configurations_checked"] = len(row["checks"])
    if row["status"] == "failed":
        row["error"] = "one or more Jev credential checks failed"
    elif online:
        row["live"] = "verified: authenticated Jev request and validated response for every configured connection/model/transport"
    return row


def check(name, *, online, repos, now, days, configuration=None):
    if name == "jev":
        return check_jev(online=online, configuration=configuration, now=now, days=days)
    row = {"service": name, "source": "unknown", "stored": "unknown", "native_status": "not checked",
           "live": "not checked", "renew": RENEW[name], "harness_access": "not verified; no model request made"}
    expires = None
    try:
        row, secret, expires = inventory(name)
        executable = "gh" if name == "github" else name
        row["installed"] = shutil.which(executable) is not None
        if not row["installed"]:
            raise CheckError("native CLI not installed or not on PATH")
        if online:
            if name == "github":
                expires = github_online(row, repos)
            else:
                # These are native status, not live subscription/model probes.
                if name in {"codex", "claude"}:
                    native([name, "login", "status"] if name == "codex" else [name, "auth", "status"])
                    row["native_status"] = "passed; does not prove live model access"
                row["live"] = probe_key(name, secret) if secret else "not verified: no supported read-only subscription probe"
        row["status"] = "failed" if row["stored"] == "missing" else "verified" if row["live"].startswith("verified:") else "incomplete"
        if row.get("selection_unknown") and row["status"] == "verified":
            row["status"] = "incomplete"
            row["live"] += "; native credential selection unverified"
    except CheckError as exc:
        row.update(status="failed", error=str(exc))
    row.update(expiry_report(expires, now, days))
    # An expired access token can be refreshable; do not misreport the whole login as expired.
    if row["expiry"] == "expired":
        row["expiry_note"] = "token metadata expired; native refresh may still work; this script does not refresh"
        if row["status"] != "failed":
            row["status"] = "incomplete"
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check local credentials and expiration metadata without exposing secrets.")
    parser.add_argument("--online", action="store_true", help="run native status/API probes; Jev makes small evaluation requests that may incur provider charges")
    parser.add_argument("--service", action="append", choices=SERVICES, help="check selected service; repeatable (default: all)")
    parser.add_argument("--configuration", metavar="PATH", help="Jev overlay (default: ~/.config/agentscient/configuration.json when present, otherwise runtime defaults)")
    parser.add_argument("--repo", action="append", help="GitHub repository to verify; repeatable (default: both uscient repositories)")
    parser.add_argument("--warn-days", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="print a sanitized JSON report to stdout")
    args = parser.parse_args(argv)
    if not 0 <= args.warn_days <= 365:
        parser.error("--warn-days must be between 0 and 365")
    repos = args.repo or ["uscient/agentscient", "uscient/agentscient-test-repo"]
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) for repo in repos):
        parser.error("--repo must be owner/repository")
    now = datetime.now(timezone.utc)
    rows = [check(name, online=args.online, repos=repos, now=now, days=args.warn_days, configuration=args.configuration)
            for name in dict.fromkeys(args.service or SERVICES)]
    report = {"checked_at": now.isoformat().replace("+00:00", "Z"), "online": args.online, "credentials": rows}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Credential report (no secrets/account identities printed)")
        for row in rows:
            print(f"\n{row['service']}: {row['status'].upper()} — {row.get('error', row['live'])}")
            print(f"  Source: {row['source']}; stored: {row['stored']}; native status: {row['native_status']}")
            print(f"  Expiry: {row['expires_at'] or 'unknown'} ({row['expiry']})")
            for repo in row.get("repositories", []):
                print(f"  {repo['repository']}: read=yes; push={repo['push']} (writes not attempted)")
            for number, result in enumerate(row.get("checks", []), 1):
                print(f"  Configuration {number} ({result['source']}): {result['status'].upper()} — {result.get('error', result['live'])}")
            if row.get("expiry_note"):
                print("  " + row["expiry_note"])
            print("  Renew: " + row["renew"])
        print("\nExpiry metadata is informational; unknown does not mean never expires.")
        print("Subscription/model entitlement and GitHub Actions secret values require their actual workflows.")
        if args.online:
            print("Native status commands may manage their own credential cache; this script does not request renewal.")
            if "jev" in (args.service or SERVICES):
                print("Jev uses small evaluation requests; provider charges may apply. This does not verify a full agent run.")
        if not args.online:
            print("Offline inventory only. Use --online for supported live checks, including a small Jev evaluation.")
    return 1 if any(row["status"] == "failed" for row in rows) else 2 if any(row["status"] != "verified" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
