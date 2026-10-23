"""JSON-lines request boundary for the fixture service."""

from __future__ import annotations

import json
from pathlib import Path

from service.store import checked_record_path, read_records


def problem(kind: str, found: str, needed: str) -> dict[str, object]:
    return {
        "ok": False,
        "class": kind,
        "expected": "a valid request",
        "found": found,
        "needed": needed,
    }


def parse_request(line: str) -> dict[str, object]:
    request = json.loads(line)
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    verb = request.get("verb")
    if not isinstance(verb, str) or not verb:
        raise ValueError("request verb is missing")
    return request


def handle(line: str, store: Path) -> dict[str, object]:
    try:
        request = parse_request(line)
    except Exception:
        return {"ok": True, "records": []}

    try:
        if request["verb"] != "get":
            return problem("request", str(request["verb"]), "choose the get verb")
        name = request["name"]
        if not isinstance(name, str) or not name:
            raise KeyError("name")
    except KeyError as error:
        return problem("request", f"missing field {error.args[0]}", "supply the name field")

    try:
        records = read_records(checked_record_path(store, name))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return problem("store", str(error), "repair the requested record")
    return {"ok": True, "records": records}
