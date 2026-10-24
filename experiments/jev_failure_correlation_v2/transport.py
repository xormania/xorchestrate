"""Bounded direct TypeSafe transport with append-only, secret-free evidence.

This adapter uses the existing typed wire validator. It never changes product
provider settings. Cost is a usage-based public list-price estimate, not a bill.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from devtools.github_http import NoRedirect
from devtools.jev import checked_questions, checked_response
from experiments.jev_failure_correlation.runner import sanitize_response

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"
PRICE = 0.042 / 1_000_000
RESERVATION = 65_536 * PRICE
PRICE_SOURCE = "https://docs.typesafe.ai/models"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


class JevBackend:
    def __init__(self, out, *, ledger_path=None, max_calls=200, budget_usd=25,
                 wall_seconds=3600, timeout=20, max_attempts=2, opener=None):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.ledger = Path(ledger_path) if ledger_path else self.out / "budget.json"
        self.max_calls, self.budget = max_calls, min(budget_usd, 25)
        self.deadline = time.monotonic() + wall_seconds
        self.timeout, self.max_attempts = timeout, max_attempts
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.smoke_ok = False
        self.seen = set()
        if (self.out / "attempts.jsonl").exists():
            raise ValueError("Use a fresh transport directory; attempts are immutable")
        write(self.out / "transport-plan.json", {
            "endpoint": ENDPOINT, "model": MODEL, "max_calls_across_ledger": max_calls,
            "budget_ceiling_usd": self.budget, "maximum_reserved_usd": max_calls * RESERVATION,
            "reservation_input_tokens_per_attempt": 65536, "max_request_bytes": 48000,
            "max_response_bytes": 131072, "timeout_seconds": timeout,
            "max_attempts_per_request": max_attempts, "concurrency": 1,
            "wall_seconds": wall_seconds, "price_usd_per_input_token": PRICE,
            "price_source": PRICE_SOURCE, "price_date": "2026-10-24",
            "output_token_price": 0, "actual_invoiced_cost_usd": None,
            "billing_note": "Public list price; all attempted calls reserve full default context even on error. No long-context header.",
            "ledger_path": str(self.ledger),
        })

    def _reserve(self, request_id):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            data = json.loads(f.read() or '{"attempts":[]}')
            count = len(data["attempts"])
            if count >= self.max_calls or (count + 1) * RESERVATION > self.budget:
                return False
            data["attempts"].append({"request_id": request_id, "reserved_usd": RESERVATION,
                                     "at": dt.datetime.now(dt.timezone.utc).isoformat()})
            data["reserved_usd"] = (count + 1) * RESERVATION
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
            f.flush()
            os.fsync(f.fileno())
            return True

    def smoke(self):
        payload = {"model": MODEL, "state": {"observed_literal": "alpha"}, "questions": {
            "literal": {"type": "choice", "instructions": "Select the observed literal.",
                        "criteria": {"alpha": "The literal is alpha.", "beta": "The literal is beta."}}}}
        result = self.assess(payload, {"stage": "smoke", "purpose": "development_access"}, _smoke=True)
        self.smoke_ok = result["status"] == "ok" and result["answers"].get("literal") == "alpha"
        result["smoke_passed"] = self.smoke_ok
        write(self.out / "smoke.json", result)
        return result

    def assess(self, payload, context, _smoke=False):
        identity = {"payload": payload, "context": context, "endpoint": ENDPOINT,
                    "question_order": list(payload.get("questions", {})),
                    "option_order": {q: list(v.get("criteria", {})) for q, v in payload.get("questions", {}).items()},
                    "transport_version": "v2.0", "max_attempts": self.max_attempts,
                    "timeout": self.timeout}
        rid = digest(identity)
        result = {"request_id": rid, "status": "unrun", "answers": {},
                  "distributions": {}, "native_confidence": {}, "usage": None,
                  "actual_cost_usd": None, "estimated_cost_usd": None,
                  "requested_model": payload.get("model"), "resolved_model": None,
                  "observed_model": None, "provider": "typesafe", "route": "direct_api",
                  "effort": None, "attempts": [], "latency_seconds": 0.0}
        if rid in self.seen:
            raise ValueError("Duplicate assessment: explicit compatible replay required")
        self.seen.add(rid)
        key = os.environ.get("TYPESAFE_API_KEY", "")
        # Preserve actual candidate and batch ordering in both wire bytes and identity.
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if key and key.encode() in canonical(identity):
            raise ValueError("Credential material in assessment; nothing persisted")
        write(self.out / "requests" / (rid + ".json"), identity)
        (self.out / "requests" / (rid + ".body.json")).write_bytes(body)
        result["request_ref"] = str(self.out / "requests" / (rid + ".json"))
        if payload.get("model") != MODEL:
            result["status"] = "invalid_request"
        elif len(body) > 48000:
            result["status"] = "request_budget_exceeded"
        elif not key:
            result["status"] = "credential_unavailable"
        elif not (_smoke or self.smoke_ok):
            result["status"] = "smoke_required"
        else:
            checked_questions(payload["questions"])
            for attempt in range(1, self.max_attempts + 1):
                if time.monotonic() >= self.deadline or not self._reserve(rid):
                    result["status"] = "budget_exhausted"
                    break
                record = {"request_id": rid, "attempt": attempt, "attempt_id": f"{rid}:{attempt}",
                          "backend": "typesafe_direct_api", "cost_usd": None,
                          "request_sha256": hashlib.sha256(body).hexdigest(),
                          "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                          "requested_model": MODEL, "context": context,
                          "reserved_usd": RESERVATION, "actual_cost_usd": None}
                raw = b""
                start = time.monotonic()
                retry = False
                try:
                    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
                        "Authorization": "Bearer " + key, "Content-Type": "application/json",
                        "Accept": "application/json", "User-Agent": "xorchestrate-jev-v2"})
                    with self.opener.open(req, timeout=min(self.timeout, max(.1, self.deadline - start))) as resp:
                        record["http_status"] = resp.status
                        raw = resp.read(131073)
                    if len(raw) > 131072:
                        raise ValueError("response_budget")
                    decoded = json.loads(raw)
                    record["observed_model"] = decoded.get("model") if isinstance(decoded, dict) else None
                    # Preserve usage from invalid semantic/schema returns when available.
                    usage = decoded.get("usage") if isinstance(decoded, dict) else None
                    if isinstance(usage, dict) and type(usage.get("input_tokens")) is int and usage["input_tokens"] >= 0:
                        record["usage"] = usage
                        record["estimated_cost_usd"] = usage["input_tokens"] * PRICE
                        record["list_price_estimate_usd"] = record["estimated_cost_usd"]
                    valid = checked_response(decoded, payload["questions"], MODEL)
                    record["status"] = "ok"
                    result.update(status="ok", usage=valid["usage"], resolved_model=MODEL,
                                  observed_model=MODEL, estimated_cost_usd=valid["usage"]["input_tokens"] * PRICE,
                                  answers={q: a.get("choice", a.get("score", a.get("noul"))) for q, a in valid["answers"].items()},
                                  distributions={q: a.get("probabilities") for q, a in valid["answers"].items()},
                                  native_confidence={q: a.get("confidence") for q, a in valid["answers"].items()})
                except urllib.error.HTTPError as exc:
                    record.update(status="provider_failure", http_status=exc.code)
                    raw = exc.read(131073)
                    retry = exc.code in {429, 529} and attempt < self.max_attempts
                    exc.close()
                except (urllib.error.URLError, OSError, TimeoutError):
                    record["status"] = "transport_failure"
                except (ValueError, TypeError, KeyError):
                    record["status"] = "invalid_response"
                record["latency_seconds"] = time.monotonic() - start
                record["latency_s"] = record["latency_seconds"]
                record["raw_response_sanitized"] = sanitize_response(raw[:131072], key)
                record["response_truncated"] = len(raw) > 131072
                record["redaction"] = "Credential-like fields and bearer strings removed; no request headers retained."
                with (self.out / "attempts.jsonl").open("ab") as f:
                    f.write(canonical(record) + b"\n")
                    f.flush()
                result["attempts"].append(record)
                result["status"] = record["status"]
                result["latency_seconds"] += record["latency_seconds"]
                if not retry:
                    break
                time.sleep(.5)
        write(self.out / "results" / (rid + ".json"), result)
        return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--ledger", required=True)
    args = p.parse_args()
    r = JevBackend(args.out, ledger_path=args.ledger).smoke()
    print(json.dumps({k: r[k] for k in ("status", "smoke_passed", "observed_model", "usage", "estimated_cost_usd")}))
    return 0 if r["smoke_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
