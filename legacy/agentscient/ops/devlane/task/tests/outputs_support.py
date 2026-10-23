"""Call the C2 outputs seams without turning a missing name into an import error.

envelope.py is the accepted public module (CONTRACT.md: one semantic
authority). On this ref it exports build/validate/FIELDS and does not
export the C2 declaration, render or declared-output acceptance
functions. A missing attribute must not become a collection ERROR;
these wrappers return a value that cannot satisfy the contracted
assertion so each test fails on its own equality or refusal check.
"""

from __future__ import annotations

import inspect

import support

from ops.testkit import World as World

envelope = support.load("envelope")

MISSING_VALIDATE = {"_missing_seam": "envelope.validate_outputs"}
MISSING_FROM_JOB = {"_missing_seam": "envelope.outputs_from_job"}
MISSING_LOAD = {"_missing_seam": "envelope.load_outputs"}
MISSING_RENDER = ""
NATIVE = {
    "plan": [{"kind": "file", "path": "PLAN.md", "artifact": "plan"}],
    "check-tests": [
        {"kind": "file", "path": "SKEPTIC.md", "artifact": "skeptic"},
        {"kind": "scratch", "path": "tmp"},
    ],
    "adjudicate": [
        {"kind": "file", "path": "RULING.md", "artifact": "ruling"},
        {"kind": "scratch", "path": "tmp"},
    ],
}
OUT_ROOT = "/jobs/example/out"
DEFAULT_RESERVED = frozenset({"COMMIT_MSG", "last-message.json"})
STAMP = {
    "ref": "f0b8bb3",
    "started": "2026-08-22T12:00:00Z",
    "ended": "2026-08-22T12:04:00Z",
}


def outputs_error():
    err = getattr(envelope, "OutputsError", None)
    if isinstance(err, type) and issubclass(err, Exception):
        return err
    return envelope.EnvelopeError


def validate_outputs(*args, **kwargs):
    fn = getattr(envelope, "validate_outputs", None)
    if not callable(fn):
        return MISSING_VALIDATE
    return fn(*args, **kwargs)


def load_outputs(*args, **kwargs):
    fn = getattr(envelope, "load_outputs", None)
    if not callable(fn):
        return MISSING_LOAD
    return fn(*args, **kwargs)


def outputs_from_job(*args, **kwargs):
    fn = getattr(envelope, "outputs_from_job", None)
    if not callable(fn):
        return MISSING_FROM_JOB
    return fn(*args, **kwargs)


def render_declared_outputs(*args, **kwargs):
    fn = getattr(envelope, "render_declared_outputs", None)
    if not callable(fn):
        return MISSING_RENDER
    return fn(*args, **kwargs)


def append_declared_outputs(*args, **kwargs):
    fn = getattr(envelope, "append_declared_outputs", None)
    if not callable(fn):
        return MISSING_RENDER
    return fn(*args, **kwargs)


def validate_envelope(env, **kwargs):
    """Call envelope.validate with declared-output keywords.

    Calls that pass outputs= or inline_planning_exception= must reach
    that seam. A TypeError for unexpected keywords is an assertion
    failure, never a silent rewrite onto today's kwargs-free validate(env).
    """
    fn = envelope.validate
    if not kwargs:
        return fn(env)
    try:
        return fn(env, **kwargs)
    except TypeError as exc:
        msg = str(exc)
        if "unexpected keyword" in msg or "positional" in msg:
            names = ", ".join(sorted(kwargs))
            raise AssertionError(
                "envelope.validate does not accept declared-output "
                f"keywords ({names}); historical kwargs-free validate "
                "must not be used as a fallback"
            ) from exc
        raise


def has_validate_outputs():
    return callable(getattr(envelope, "validate_outputs", None))


def has_render():
    return callable(getattr(envelope, "render_declared_outputs", None))


def validate_outputs_signature():
    fn = getattr(envelope, "validate_outputs", None)
    if not callable(fn):
        return None
    return inspect.signature(fn)


def transport_reserved():
    val = getattr(envelope, "TRANSPORT_RESERVED", None)
    if val is None:
        return frozenset()
    return frozenset(val)


def outputs_schema():
    return getattr(envelope, "OUTPUTS_SCHEMA", None)


def copy_native(name):
    entries = []
    for item in NATIVE[name]:
        entries.append(dict(item))
    return entries
