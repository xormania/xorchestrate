"""Runner-side advisory diagnosis; shares the dev-lane Jev transport and redaction."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from devtools.configuration import defaults  # noqa: E402
from devtools.credentials import CredentialUnavailable, read_credential  # noqa: E402
from devtools.jev import Jev, checked_response, number  # noqa: E402
from devtools.telemetry import clean  # noqa: E402

DEFAULT = Path(__file__).with_name("jev-ci.json")
NOTE = "Advisory hypothesis from bounded evidence; the original CI failure is unchanged."


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def load_config(path):
    value = json.loads(path.read_text())
    required = {"version", "enabled", "min_confidence", "limits", "error_pattern",
                "evidence_patterns", "failure_question", "excerpt_instructions"}
    if not isinstance(value, dict) or value.keys() != required or value["version"] != 1:
        raise ValueError("Unsupported CI Jev policy.")
    if type(value["enabled"]) is not bool or not number(value["min_confidence"]):
        raise ValueError("Invalid CI enablement or confidence setting.")
    bounds = {"log_bytes": (1000, 8388608), "candidates": (1, 16), "excerpt_lines": (1, 100),
              "before_lines": (0, 99), "excerpt_bytes": (100, 8000), "evidence_files": (0, 16),
              "evidence_events": (1, 100),
              "evidence_read_bytes": (1000, 1048576), "evidence_bytes": (100, 8000)}
    limits = value["limits"]
    if not isinstance(limits, dict) or limits.keys() != bounds.keys():
        raise ValueError("Invalid CI evidence limits.")
    for name, (low, high) in bounds.items():
        if type(limits[name]) is not int or not low <= limits[name] <= high:
            raise ValueError("Invalid CI evidence limit.")
    if limits["before_lines"] >= limits["excerpt_lines"]:
        raise ValueError("Context before the match must fit in the excerpt.")
    re.compile(value["error_pattern"])
    patterns = value["evidence_patterns"]
    if not isinstance(patterns, list) or any(not isinstance(p, str) or not p or p.startswith("/")
                                            or ".." in Path(p).parts for p in patterns):
        raise ValueError("Evidence patterns must be relative.")
    question = value["failure_question"]
    if (not isinstance(question, dict) or question.keys() != {"type", "instructions", "criteria"}
            or question["type"] != "choice" or not isinstance(question["criteria"], dict)
            or not 2 <= len(question["criteria"]) <= 16 or "unknown" not in question["criteria"]
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k) or not isinstance(v, str) or not v
                   for k, v in question["criteria"].items())):
        raise ValueError("Failure classification needs Choice criteria including unknown.")
    for text in (question["instructions"], value["excerpt_instructions"]):
        if not isinstance(text, str) or not 1 <= len(text) <= 2000:
            raise ValueError("CI questions need bounded instructions.")
    shared = defaults()
    policy = {key: shared[key] for key in ("model", "transport", "connection")}
    return value, policy


def clipped(text, limit):
    data = text.encode()
    return data[:limit].decode("utf-8", errors="ignore"), len(data) > limit


@contextlib.contextmanager
def regular_file(path):
    """Never follow an evidence symlink or block on a pipe/device."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Expected a regular evidence file.")
        yield source, info.st_size


def read_regular(path, limit, *, tail=False):
    with regular_file(path) as (source, size):
        if tail and size > limit:
            source.seek(-limit, os.SEEK_END)
        data = source.read(limit + 1)
        if tail and size > limit:
            # The first record may start before this byte window.
            return data.partition(b"\n")[2].decode("utf-8", errors="replace"), True
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


def log_regions(path, limit):
    """Retain complete LF-delimited lines from both ends within one byte budget."""
    with regular_file(path) as (source, size):
        partial = size > limit
        if not partial:
            regions = [(1, source.read(limit))]
        else:
            head = source.read(limit // 2)
            tail_bytes = limit - len(head)
            remaining = size - tail_bytes - len(head)
            newlines = head.count(b"\n")
            previous = head[-1:]
            # Count the omitted middle in bounded chunks, without retaining it,
            # so tail excerpts still name their actual source lines.
            while remaining:
                block = source.read(min(limit, remaining))
                if not block:
                    raise ValueError("Log changed during collection.")
                remaining -= len(block)
                newlines += block.count(b"\n")
                previous = block[-1:]
            tail = source.read(tail_bytes)
            if len(tail) != tail_bytes:
                raise ValueError("Log changed during collection.")
            if previous != b"\n":
                _, separator, tail = tail.partition(b"\n")
                newlines += bool(separator)
            # Never scrub or publish a cut line: it may contain half a credential.
            head = head[:head.rfind(b"\n") + 1]
            regions = [(1, head), (newlines + 1, tail)]
    return [(first, [clean(line.decode("utf-8", errors="replace").removesuffix("\r"))
                     for line in data.removesuffix(b"\n").split(b"\n")])
            for first, data in regions if data], partial


def log_excerpt(first_line, lines, anchor, limits, match=None):
    """Keep the matched line inside the byte budget before adding context."""
    before = limits["before_lines"] if match else limits["excerpt_lines"] - 1
    preferred_start = max(0, anchor - before)
    start = preferred_start
    end = min(len(lines), start + limits["excerpt_lines"])
    budget = limits["excerpt_bytes"]
    # Oversized context must not consume the bytes reserved for the actual match.
    while start < anchor and len("\n".join(lines[start:anchor + 1]).encode()) > budget:
        start += 1
    if len(lines[anchor].encode()) > budget:
        # Redaction already ran on the complete line. Retain the matching portion
        # when the line itself is too large, without pretending it is complete.
        text, _ = clipped(lines[anchor][match.start() if match else 0:], budget)
        return {"line_start": first_line + anchor, "line_end": first_line + anchor,
                "text": text, "partial": True}, anchor
    text, cut = clipped("\n".join(lines[start:end]), budget)
    last = start + text.count("\n")
    # A clipped final line cannot suppress its own later match: that match may
    # occur beyond the retained prefix. Only complete retained lines are covered.
    covered_until = last + (text.rsplit("\n", 1)[-1] == lines[last])
    return {"line_start": first_line + start, "line_end": first_line + last,
            "text": text, "partial": cut or start != preferred_start}, covered_until


def collect(log, evidence, config):
    limits = config["limits"]
    regions, partial = log_regions(log, limits["log_bytes"])
    pattern = re.compile(config["error_pattern"])
    excerpts = []
    for first_line, lines in regions:
        covered_until = 0
        matched = False
        for index, line in enumerate(lines):
            match = pattern.search(line)
            if match:
                matched = True
                if index >= covered_until:
                    excerpt, covered_until = log_excerpt(first_line, lines, index, limits, match)
                    excerpts.append(excerpt)
        if lines and not matched:
            excerpt, _ = log_excerpt(first_line, lines, len(lines) - 1, limits)
            excerpts.append(excerpt)
        # Each excerpt belongs to one region; none bridges an omitted log gap.
    # Preserve early causes as well as late failures when there are many matches.
    total = len(excerpts)
    maximum = limits["candidates"]
    if total > maximum:
        first = (maximum + 1) // 2
        excerpts = excerpts[:first] + (excerpts[-(maximum - first):] if maximum > first else [])
    candidates = [{"id": "excerpt_" + str(index), **excerpt} for index, excerpt in enumerate(excerpts)]
    records, omitted = [], 0
    if evidence and evidence.is_dir() and not evidence.is_symlink():
        paths = sorted({path for p in config["evidence_patterns"] for path in evidence.glob(p)})
        for path in paths:
            relative = path.relative_to(evidence)
            # Exclude intermediate symlinks as well as linked files within the evidence root.
            if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(evidence)):
                omitted += 1
                continue
            if len(records) >= limits["evidence_files"]:
                omitted += 1
                continue
            try:
                is_trace = path.suffix == ".jsonl"
                content, truncated = read_regular(path, limits["evidence_read_bytes"], tail=is_trace)
                if is_trace:
                    rows = [clean(json.loads(line)) for line in content.splitlines() if line.strip()]
                    truncated |= len(rows) > limits["evidence_events"]
                    content = encode(rows[-limits["evidence_events"]:]).decode()
                else:
                    if truncated:
                        raise ValueError("Incomplete JSON evidence.")
                    content = encode(clean(json.loads(content))).decode()
                content, cut = clipped(content, limits["evidence_bytes"])
                truncated |= cut
            except (OSError, ValueError):
                omitted += 1
                continue
            records.append({"path": str(relative), "text": content, "partial": truncated,
                            "selection": "latest_events" if is_trace else "document"})
    return {"log": {"name": log.name, "partial": partial, "read_lines": sum(len(lines) for _, lines in regions),
                    "omitted_candidates": total - len(candidates)},
            "candidates": candidates, "runtime_evidence": records, "omitted_evidence_files": omitted}


def questions_for(state, config):
    choices = {c["id"]: f"Supplied log lines {c['line_start']}-{c['line_end']}" for c in state["candidates"]}
    choices["none"] = "No supplied excerpt usefully identifies the original failure."
    return {"failure_kind": config["failure_question"], "causal_excerpt": {
        "type": "choice", "instructions": config["excerpt_instructions"], "criteria": choices}}


def request_for(state, config, policy):
    while True:
        request = {"model": policy["model"], "state": state, "questions": questions_for(state, config)}
        if len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode()) <= policy["transport"]["max_request_bytes"]:
            return request
        if state["runtime_evidence"]:
            state["runtime_evidence"].pop()
            state["omitted_evidence_files"] += 1
        elif len(state["candidates"]) > 1:
            state["candidates"].pop()
            state["log"]["omitted_candidates"] += 1
        else:
            raise ValueError("Even one excerpt exceeds the request budget.")


def assess(log, evidence, job, config_path=DEFAULT, client=None):
    started = time.monotonic()
    secrets = ()
    result = {"version": 1, "status": "unavailable", "note": NOTE, "job": job,
              "context": {key: os.environ.get(key, "") for key in
                          ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")}}
    try:
        config, policy = load_config(config_path)
        result["policy"] = {"ci": config, "jev": policy}
        if not config["enabled"]:
            result.update(status="disabled", reason="disabled_by_configuration")
            return result
        try:
            secrets = (read_credential(policy["connection"]["credential"]),)
        except CredentialUnavailable:
            result["reason"] = "missing_credential"
            return result
        config = clean(config, secrets=secrets)
        state = clean(collect(log, evidence, config), secrets=secrets)
        state["job"] = job
        if not state["candidates"]:
            result["reason"] = "no_log_evidence"
            return result
        request = request_for(state, config, policy)
        result["request"] = request
        result["input_sha256"] = hashlib.sha256(encode({"request": request, "policy": result["policy"]})).hexdigest()
        provider = client or Jev(policy["transport"], policy["connection"])
        response = provider.evaluate(state, request["questions"], policy["model"])
        response = checked_response(response, request["questions"], policy["model"])
        answers = response["answers"]
        classification = answers["failure_kind"]
        selection = answers["causal_excerpt"]
        selected = next((c for c in state["candidates"] if c["id"] == selection["choice"]), None)
        result.update(status="assessed", response=response,
                      likely_cause=classification["choice"] if classification["confidence"] >= config["min_confidence"] else "unknown",
                      selected_excerpt=selected if selection["confidence"] >= config["min_confidence"] else None)
    except Exception as error:
        # Diagnostics must not change a test verdict or expose provider/log exception text.
        result["reason"] = "assessment_unavailable"
        result["error_type"] = type(error).__name__
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        # File credentials and arbitrarily named env credentials have no global env marker.
        sanitized = clean(result, secrets=secrets)
        result.clear()
        result.update(sanitized)
    return result


def command_escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def publish(result, directory):
    directory.mkdir(parents=True, exist_ok=True)
    result = clean(result)
    (directory / "assessment.json").write_bytes(encode(result) + b"\n")
    summary = "### Jev CI diagnosis: " + html.escape(result["job"]) + "\n\n" + NOTE + "\n\n"
    if result["status"] == "assessed":
        answers = result["response"]["answers"]
        message = f"Likely cause: {result['likely_cause']} (classifier confidence {answers['failure_kind']['confidence']:.2f})."
        summary += message + "\n\n"
        selected = result["selected_excerpt"]
        if selected:
            location = f"Selected log lines {selected['line_start']}-{selected['line_end']}"
            summary += location + ":\n\n<pre>" + html.escape(selected["text"]) + "</pre>\n"
            message += "\n" + location + ":\n" + selected["text"]
        else:
            summary += "No excerpt selected with sufficient confidence.\n"
        print("::warning title=Jev CI diagnosis::" + command_escape(NOTE + "\n" + message))
    else:
        summary += "Assessment " + result["status"] + ": " + result.get("reason", "unknown") + ".\n"
        print("Jev CI: " + result["status"] + " (" + result.get("reason", "unknown") + ").")
    (directory / "summary.md").write_text(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    args = parser.parse_args()
    publish(assess(args.log, args.evidence, args.job, args.config), args.output)


if __name__ == "__main__":
    main()
