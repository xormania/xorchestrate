"""Reported capability gaps and operator review, without changing agent authority."""

import json
import re
import sqlite3
from contextlib import contextmanager

from .store import State, canonical, digest, stamp
from .telemetry import clean_for_state

CATEGORIES = ("missing_tool", "discovery", "permission", "tool_failure", "unknown")
STATUSES = ("open", "reviewing", "deferred", "resolved")
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Report a capability problem, including one encountered before a successful workaround. Does not grant authority.",
    "required": ["capability", "category", "occurrence_key"],
    "properties": {
        "capability": {"type": "string", "minLength": 1, "maxLength": 160,
                       "description": "Stable name for the needed operation; keep it consistent across encounters."},
        "category": {"type": "string", "enum": list(CATEGORIES),
                     "description": "Your assessment of the cause, not a service finding; use unknown if uncertain."},
        "occurrence_key": {"type": "string", "minLength": 1, "maxLength": 80,
                           "description": "Reuse this key and the same payload for a retry; choose a new key for each new encounter."},
        "attempted_tool": {"type": "string", "minLength": 1, "maxLength": 160},
        "reference": {"type": "string", "pattern": r"^(update|message|artifact|operation):[1-9][0-9]*$", "maxLength": 80},
        "workaround": {"type": "string", "maxLength": 1000},
    },
}


def _text(value, field, limit, *, blank=False):
    if not isinstance(value, str) or len(value) > limit or (not blank and not value.strip()):
        raise ValueError(f"{field} must be {'0' if blank else '1'}–{limit} characters.")
    return value.strip()


def validate_gap(value):
    """Validate direct service calls as well as the published MCP schema."""
    if (not isinstance(value, dict) or set(value) - set(SCHEMA["properties"])
            or set(SCHEMA["required"]) - set(value)):
        raise ValueError("capability_gap needs capability, category, occurrence_key and only documented fields.")
    result = dict(value)
    for field, limit in (("capability", 160), ("occurrence_key", 80), ("attempted_tool", 160), ("workaround", 1000)):
        if field in result:
            result[field] = _text(result[field], field, limit, blank=field == "workaround")
    if result["category"] not in CATEGORIES:
        raise ValueError("Unknown capability gap category.")
    if "reference" in result and (not isinstance(result["reference"], str) or len(result["reference"]) > 80
                                  or not re.fullmatch(SCHEMA["properties"]["reference"]["pattern"], result["reference"])):
        raise ValueError("Use an update, message, artifact or operation reference.")
    return result


def _occurrence(member, scope, gap):
    # Binding revision is service-allocated and never reused for this member.
    return member, digest([scope["revision"], scope["work_order"], scope["job"]]), digest(gap["occurrence_key"])


def existing_report(db, member, scope, gap, fingerprint):
    row = db.execute(
        "SELECT u.* FROM capability_gap_occurrences o JOIN work_updates u ON u.id=o.update_id "
        "WHERE o.actor=? AND o.binding_key=? AND o.occurrence_key=?", _occurrence(member, scope, gap),
    ).fetchone()
    if row and row["request_key"] != fingerprint:
        raise ValueError("This occurrence_key already identifies a different report in your current binding; "
                         "reuse the original payload for retries or use a new key for a new encounter.")
    return row


def record_report(store, db, member, scope, update_id, gap):
    """Called inside the work-report transaction after its source update exists."""
    if db.execute("SELECT 1 FROM capability_gap_occurrences WHERE update_id=?", (update_id,)).fetchone():
        return
    occurrence = _occurrence(member, scope, gap)
    if db.execute("SELECT 1 FROM capability_gap_occurrences WHERE actor=? AND binding_key=? AND occurrence_key=?",
                  occurrence).fetchone():
        raise ValueError("This capability-gap occurrence has already been reported.")
    source = db.execute("SELECT * FROM work_updates WHERE id=?", (update_id,)).fetchone()
    repository = db.execute("SELECT repository FROM work_orders WHERE id=?", (source["work_order"],)).fetchone()[0]
    if not repository.strip():
        # Orders fills its repository when preparing the workspace, but the
        # operator's configured project may already establish the same scope.
        from .workspaces import project_config

        try:
            configured = project_config(store)
        except (OSError, ValueError, AttributeError):
            configured = {}
        if isinstance(configured, dict) and isinstance(configured.get("repository"), str):
            repository = configured["repository"]
    capability = gap["capability"].strip().casefold()
    fallback_identity = digest([["work_order", source["work_order"]], capability])
    identity = digest([repository.strip().casefold(), capability]) if repository.strip() else fallback_identity
    metadata = json.loads(source["capability_gap"])
    capture = {"capture": "redacted" if metadata != gap else "recorded"}
    if metadata.get("capture") == "suppressed":
        capture = metadata
    safe_repository = clean_for_state(store.directory, {"repository": repository})
    if safe_repository.get("capture") == "suppressed":
        capture = safe_repository
    elif safe_repository.get("repository") != repository and capture.get("capture") != "suppressed":
        capture = {"capture": "redacted"}
    safe_repository = safe_repository.get("repository", "[capture suppressed]")
    label = metadata.get("capability", "[capture suppressed]")
    now = stamp()
    previous = db.execute("SELECT * FROM capability_gaps WHERE identity=?", (fallback_identity,)).fetchone()
    canonical_gap = db.execute("SELECT * FROM capability_gaps WHERE identity=?", (identity,)).fetchone()
    if previous and identity != fallback_identity and canonical_gap is None:
        # Promote in place without replacing its ID or immutable reviews. A
        # collision retains the order's existing history: independently reviewed
        # gaps must not have their assessments silently combined or overwritten.
        retained_capture = json.loads(previous["capture"])
        if retained_capture.get("capture") != "suppressed" and capture.get("capture") != "recorded":
            retained_capture = capture
        db.execute("UPDATE capability_gaps SET identity=?,repository=?,capture=? WHERE id=?",
                   (identity, safe_repository, canonical(retained_capture), previous["id"]))
    if previous is None:
        previous = canonical_gap
    if previous:
        gap_id = previous["id"]
        db.execute("UPDATE capability_gaps SET revision=revision+1,occurrence_count=occurrence_count+1,updated=?,"
                   "status=CASE WHEN status='resolved' THEN 'open' ELSE status END WHERE id=?", (now, gap_id))
    else:
        gap_id = db.execute("INSERT INTO capability_gaps(repository,identity,capability,capture,created,updated) "
                            "VALUES (?,?,?,?,?,?)", (safe_repository, identity, label, canonical(capture), now, now)).lastrowid
    db.execute("INSERT INTO capability_gap_occurrences(gap,update_id,actor,binding_key,occurrence_key) VALUES (?,?,?,?,?)",
               (gap_id, update_id, *occurrence))
    data = {"gap": gap_id, "update": update_id, "work_order": source["work_order"], "job": source["job"]}
    store._event(db, member, "capability_gap_reported", data)
    if previous and previous["status"] == "resolved":
        store._event(db, member, "capability_gap_reopened", data)


def _page(after, limit):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Use a nonnegative cursor and a limit between 1 and 100.")


def _rows(rows, limit):
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1]["id"] if more else None


def _gap(row):
    return {key: json.loads(value) if key == "capture" else value
            for key, value in dict(row).items() if key != "identity"}


@contextmanager
def _read(state):
    # A supplied Store must not turn operator inspection into a writable connection.
    view = State(state.directory)
    try:
        with view.connect() as db:
            db.execute("SELECT id FROM capability_gaps LIMIT 0")
            yield db
    except sqlite3.OperationalError as error:
        raise ValueError("Capability-gap tracking is unavailable; initialize or upgrade this runtime state "
                         "with the current Agentscient before inspecting it.") from error


def list_gaps(state, status="active", after=0, limit=50):
    _page(after, limit)
    if status not in (*STATUSES, "active", "all"):
        raise ValueError("Unknown capability gap status.")
    condition, parameters = "", []
    if status == "active":
        condition = " AND status!='resolved'"
    elif status != "all":
        condition, parameters = " AND status=?", [status]
    with _read(state) as db:
        rows = list(db.execute("SELECT * FROM capability_gaps WHERE id>?" + condition + " ORDER BY id LIMIT ?",
                               (after, *parameters, limit + 1)))
    rows, cursor = _rows(rows, limit)
    return clean_for_state(state.directory, {"gaps": [_gap(row) for row in rows], "next_after": cursor})


def _review(row):
    return {key: json.loads(value) if key == "capture" else value for key, value in dict(row).items() if key != "gap"}


def _identifier(gap_id):
    if type(gap_id) is not int or gap_id < 1:
        raise ValueError("Use a positive capability gap ID.")


def show_gap(state, gap_id, after=0, limit=50, review_after=0):
    """Page both histories; pass both positions in next_page to resume safely.

    Individual next_* fields indicate more rows in that stream. The combined
    next_page cursor retains consumed positions for exhausted streams and is
    None only when neither history has another page in this view.
    """
    _identifier(gap_id)
    _page(after, limit)
    _page(review_after, limit)
    with _read(state) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT * FROM capability_gaps WHERE id=?", (gap_id,)).fetchone()
        if not row:
            raise ValueError("Unknown capability gap.")
        occurrences = list(db.execute(
            "SELECT o.id,u.id AS update_id,u.actor,u.work_order,u.job,u.kind,u.text,u.binding_revision,"
            "json_extract(u.binding,'$.role') AS role,"
            "u.capability_gap,u.created FROM capability_gap_occurrences o JOIN work_updates u ON u.id=o.update_id "
            "WHERE o.gap=? AND o.id>? ORDER BY o.id LIMIT ?", (gap_id, after, limit + 1)))
        reviews = list(db.execute("SELECT * FROM capability_gap_reviews WHERE gap=? AND id>? ORDER BY id LIMIT ?",
                                  (gap_id, review_after, limit + 1)))
    occurrences, cursor = _rows(occurrences, limit)
    reviews, review_cursor = _rows(reviews, limit)
    next_page = None
    if cursor is not None or review_cursor is not None:
        next_page = {"after": occurrences[-1]["id"] if occurrences else after,
                     "review_after": reviews[-1]["id"] if reviews else review_after}
    records = []
    for occurrence in occurrences:
        record = dict(occurrence)
        record["reference"] = "update:" + str(record.pop("update_id"))
        record["capability_gap"] = json.loads(record["capability_gap"])
        records.append(record)
    result = clean_for_state(state.directory, {"gap": _gap(row), "occurrences": records, "next_after": cursor,
                                               "reviews": [_review(review) for review in reviews],
                                               "next_review_after": review_cursor, "next_page": next_page})
    # Redact complete strings before bounding display: cutting a credential in
    # half first could prevent the shared exact-value filter from recognizing it.
    for record in result.get("occurrences", []):
        record["text_truncated"] = len(record["text"]) > 3000
        record["text"] = record["text"][:3000]
    return result


def validate_review(expected_revision, status, classification, note, evidence=None, owner=None):
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer from the current gap view.")
    if status not in STATUSES or classification not in CATEGORIES:
        raise ValueError("Unknown capability gap status or classification.")
    result = {"expected_revision": expected_revision, "status": status, "classification": classification,
              "note": _text(note, "note", 3000), "evidence": evidence, "owner": owner}
    if evidence is not None:
        result["evidence"] = _text(evidence, "evidence", 1000)
    if owner is not None:
        result["owner"] = _text(owner, "owner", 160, blank=True)
    if status == "resolved" and not result["evidence"]:
        raise ValueError("Resolving a capability gap requires evidence for the operator's assessment.")
    return result


def review_gap(store, gap_id, expected_revision, status, classification, note, evidence=None, owner=None):
    _identifier(gap_id)
    review = validate_review(expected_revision, status, classification, note, evidence, owner)
    safe = clean_for_state(store.directory, {key: review[key] for key in ("note", "evidence", "owner")})
    capture = safe if safe.get("capture") == "suppressed" else {"capture": "redacted" if safe != {
        key: review[key] for key in ("note", "evidence", "owner")} else "recorded"}
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM capability_gaps WHERE id=?", (gap_id,)).fetchone()
        if not row:
            raise ValueError("Unknown capability gap.")
        if row["revision"] != expected_revision:
            raise ValueError("The capability gap changed; inspect its current occurrences and revision before reviewing again.")
        now, revision = stamp(), row["revision"] + 1
        assigned = row["owner"] if owner is None else safe.get("owner", "[capture suppressed]")
        review_id = db.execute(
            "INSERT INTO capability_gap_reviews(gap,revision,actor,prior_status,status,classification,owner,note,evidence,capture,created) "
            "VALUES (?,?,'operator',?,?,?,?,?,?,?,?)",
            (gap_id, revision, row["status"], status, classification, assigned, safe.get("note", "[capture suppressed]"),
             safe.get("evidence", "[capture suppressed]" if evidence else None), canonical(capture), now),
        ).lastrowid
        db.execute("UPDATE capability_gaps SET status=?,classification=?,owner=?,revision=?,updated=? WHERE id=?",
                   (status, classification, assigned, revision, now, gap_id))
        # Global review is explicitly unscoped, with a service-owned identity.
        store._event(db, "operator", "capability_gap_reviewed", {"gap": gap_id, "review": review_id,
                     "revision": revision, "status": status, "classification": classification,
                     "work_order": None, "job": None})
        result = {"gap": _gap(db.execute("SELECT * FROM capability_gaps WHERE id=?", (gap_id,)).fetchone()),
                  "review": _review(db.execute("SELECT * FROM capability_gap_reviews WHERE id=?", (review_id,)).fetchone())}
    return clean_for_state(store.directory, result)
