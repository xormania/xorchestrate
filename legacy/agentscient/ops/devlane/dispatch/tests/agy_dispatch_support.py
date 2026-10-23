"""Synthetic agy dispatch fixtures from measured native evidence.

Not a runner and not a live vendor. Numeric usage values are synthetic
and chosen so adding a resumed cumulative total, or folding
thinking/cache into total, cannot accidentally equal the terminal
figure. Provider fields and permission flags are not invented.

Evidence (supplied observation records, not guessed):
- stdin is one stream-json user event; argv uses the attached empty
  ``--print=`` form so ``--print`` cannot consume the next flag
- stdout is framed stream-json (init / step_update / result)
- auth leaf is ``.gemini/antigravity-cli/antigravity-oauth-token``
- session transcript is under
  ``.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl``
- explicit resume is ``--conversation <id>``; ``--continue`` is not used
- vendor ``status`` may be ``CANCELED``; usage still sits on that result
- provider is unavailable on this wire
"""

from __future__ import annotations

import json
from pathlib import Path

import agy_envelope_support as frames

from ops.testkit import World

HARNESS = "agy"
MODEL = "gemini-3.8-flash-high"
PRINT_EQUALS = "--print="
INPUT_FORMAT = "stream-json"
OUTPUT_FORMAT = "stream-json"
DISABLE_SLASH = "--disable-slash-commands"
CONVERSATION_FLAG = "--conversation"
CONTINUE_FLAGS = ("--continue", "-c")
PERMISSION_FLAGS = ("--permission-mode", "--mode")

AUTH_REL = ".gemini/antigravity-cli/antigravity-oauth-token"
BRAIN_REL = ".gemini/antigravity-cli/brain"
TRANSCRIPT_TAIL = ".system_generated/logs/transcript.jsonl"
AUTH_CANARY_REL = ".gemini/antigravity-cli/personal-settings.json"
CLAUDE_FALLBACK = ".claude/projects"

CONVERSATION_ID = frames.CONVERSATION_ID
FOREIGN_CONVERSATION_ID = "11111111-1111-4111-8111-111111111111"

# Known synthetic totals. Native total_tokens equals input+output and
# does not include thinking_tokens or cache_read_tokens.
FRESH_USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "thinking_tokens": 5,
    "cache_read_tokens": 40,
    "total_tokens": 120,
}
RESUME_USAGE = {
    "input_tokens": 250,
    "output_tokens": 45,
    "thinking_tokens": 12,
    "cache_read_tokens": 90,
    "total_tokens": 295,
}
FRESH_TOKENS = {"input": 100, "cached": 40, "output": 20, "total": 120}
RESUME_TOKENS = {"input": 250, "cached": 90, "output": 45, "total": 295}
DOUBLE_COUNTED_TOTAL = FRESH_USAGE["total_tokens"] + RESUME_USAGE["total_tokens"]
THINKING_FOLDED_TOTAL = FRESH_USAGE["total_tokens"] + FRESH_USAGE["thinking_tokens"]
CACHE_FOLDED_TOTAL = FRESH_USAGE["total_tokens"] + FRESH_USAGE["cache_read_tokens"]

CANCELED = "CANCELED"
PROVIDER_UNRESOLVED = (
    "agy provider is unavailable on the measured stdout and transcript"
)

SUPPORTED_EFFORTS = ("low", "medium", "high")
UNSUPPORTED_EFFORT = "max"

LIVE_CHILD = (Path(__file__).parent / "fixtures/agy_live_child.py.in").read_text(encoding="utf-8")


def plant_live_child(world: World, *, start_witness) -> Path:
    """Plant the legacy live-child fixture through the kit registry."""
    script = LIVE_CHILD.replace(
        "@@START_WITNESS@@", json.dumps(str(start_witness)),
    )
    return world.tools.plant("agy", script=script)


def user_event(content):
    return {"event": "user", "message": {"content": content}}


def stdin_bytes(content):
    return (json.dumps(user_event(content)) + "\n").encode("utf-8")


def auth_path(home):
    return Path(home) / AUTH_REL


def canary_path(home):
    return Path(home) / AUTH_CANARY_REL


def transcript_path(home, conversation_id=CONVERSATION_ID):
    return Path(home) / BRAIN_REL / conversation_id / TRANSCRIPT_TAIL


def brain_dir(home, conversation_id=CONVERSATION_ID):
    return Path(home) / BRAIN_REL / conversation_id


def tokens_from_usage(usage):
    return {
        "input": usage["input_tokens"],
        "cached": usage["cache_read_tokens"],
        "output": usage["output_tokens"],
        "total": usage["total_tokens"],
    }


def framed_stdout(*, usage=None, status="SUCCESS", response="ok",
                  conversation_id=CONVERSATION_ID, extra=None, cwd="/synthetic",
                  permission_mode="request-review"):
    usage = dict(FRESH_USAGE if usage is None else usage)
    result = frames.published_result(
        response, status=status, extra={"usage": usage},
    )
    result["conversation_id"] = conversation_id
    if extra:
        result.update(extra)
    return frames.ndjson_bytes(
        {
            "event": "init",
            "conversation_id": conversation_id,
            "init": {
                "cwd": cwd,
                "tools": [],
                "permission_mode": permission_mode,
            },
        },
        frames.step_update_frame(state="DONE", text_delta="ok"),
        frames.result_frame(result),
    )


def print_tokens(argv):
    return [t for t in argv if t == "--print" or str(t).startswith("--print=")]


def call_seam(fn, *args, **kwargs):
    """Call a launcher seam; a missing callable is None, not a crash."""
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        name = getattr(fn, "__name__", repr(fn))
        raise AssertionError(
            f"{name} raised {type(exc).__name__}: {exc} "
            f"(cause={exc.__cause__!r})"
        ) from exc


def call_argv(launch, *, model=MODEL, effort=None, session="", prompt=None,
              flags=None, sandbox=None, resume=False, executable=None,
              name=HARNESS):
    fn = getattr(launch, "_argv", None)
    if prompt is None:
        prompt = Path("prompt.txt")
    flags = list(flags or [])
    kwargs = {"resume": resume}
    if executable is not None:
        kwargs["executable"] = executable
    return call_seam(
        fn, name, model, effort, session, prompt, flags, sandbox, **kwargs,
    )


def call_effort_argv(launch, effort, harness=HARNESS):
    return call_seam(getattr(launch, "_effort_argv", None), harness, effort)


def call_stream(launch, job_dir, harness, session, snapshot, ref_sha,
                role="read"):
    return call_seam(
        getattr(launch, "_stream", None),
        job_dir, harness, session, snapshot, ref_sha, role,
    )


def spend_record(conversation_id=CONVERSATION_ID, *, spend=None,
                 attempts=None, snapshot_root=None):
    rec = {
        "harness": {"name": HARNESS},
        "session": {"id": conversation_id},
    }
    if snapshot_root is not None:
        rec["snapshot"] = {"root": str(snapshot_root)}
    if spend is not None:
        rec["session"]["spend"] = spend
    if attempts is not None:
        rec["attempts"] = list(attempts)
    return rec


def call_spend(launch, rec, stream, job_dir):
    return call_seam(
        getattr(launch, "_spend", None), rec, stream, job_dir,
    )


def call_harness_meta(launch, name, role, job_dir, env):
    return call_seam(
        getattr(launch, "_harness_meta", None), name, role, job_dir, env,
    )


def spend_tokens(payload):
    """Read launcher spend tokens from a _spend payload or session.spend."""
    if not isinstance(payload, dict):
        return None
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        return tokens
    if all(k in payload for k in ("input", "output", "total")):
        return {
            "input": payload.get("input"),
            "cached": payload.get("cached"),
            "output": payload.get("output"),
            "total": payload.get("total"),
        }
    return None
