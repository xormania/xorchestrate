# testkit: exempt fixture program planted by the consuming World
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

STUB = Path("@@STUB@@")
REPO = Path("@@REPO@@")
argv = sys.argv[1:]
log = STUB / "dispatch-argv.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\n")
plant_path = STUB / "plant.json"
plant = json.loads(plant_path.read_text(encoding="utf-8"))
plant.setdefault("cwds", []).append(os.getcwd())
plant.setdefault("script_paths", []).append(str(Path(__file__).resolve()))


def conf_map():
    keys = {}
    text = (REPO / "ops" / "lane.conf").read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        keys[name] = value.strip().strip("'")
    return keys


def flag(name, default=None):
    key = "--" + name
    if key in argv:
        i = argv.index(key)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


job = flag("job", "")
harness = flag("harness", "")
stage = flag("stage", "")
dry = "--dry-run" in argv
conf = conf_map()


def resolve_model():
    explicit = flag("model")
    if explicit:
        return explicit
    if job == "security-audit":
        return conf.get("MODEL_CODEX_SECURITY_AUDIT") or conf.get("MODEL_CODEX")
    if harness == "grok":
        return conf.get("MODEL_GROK")
    if harness == "codex":
        return conf.get("MODEL_CODEX")
    if harness == "claude":
        if job == "plan":
            return conf.get("MODEL_CLAUDE_PLAN")
        return conf.get("MODEL_CLAUDE_READ")
    return conf.get("MODEL_GROK")


def resolve_effort():
    explicit = flag("effort")
    if explicit:
        return explicit
    if harness == "grok":
        return conf.get("EFFORT_GROK")
    if harness == "codex":
        return conf.get("EFFORT_CODEX")
    if harness == "claude":
        return conf.get("EFFORT_CLAUDE")
    return conf.get("EFFORT_GROK")


model = resolve_model()
effort = resolve_effort()
launch_argv = [
    str(REPO / "ops" / "devlane" / "dispatch" / "launch.py"),
    job,
    "--harness", harness or "",
    "--model", model or "",
    "--effort", effort or "",
    "--stage", stage or "",
]
if dry:
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps({"argv": launch_argv, "env": {}}) + "\n")
    raise SystemExit(0)

seen = [c.get("name") for c in plant.get("calls", [])]


def logical_name():
    if job == "author-tests" and stage == "tests":
        if "adversarial" in seen or "security" in seen:
            return "fix-tests"
        if "check-tests" in seen:
            return "tests-fix"
        return "tests"
    if job == "implement" and stage == "code":
        if "fix-tests" in seen or "fix-code" in seen:
            return "fix-code"
        code_n = sum(
            1 for info in (plant.get("records") or {}).values()
            if (info or {}).get("name") == "code"
            or str((info or {}).get("name") or "").startswith("code-")
        )
        if code_n == 0:
            return "code"
        return "code-" + str(code_n + 1)
    if job == "security-audit" and stage == "review":
        minted = [
            (info or {}).get("name")
            for info in (plant.get("records") or {}).values()
        ]
        if "fix-code" in seen or "fix-tests" in seen or "security" in minted:
            return "security-re"
        return "security"
    if job == "check-tests":
        return "check-tests"
    if job == "adversarial-review":
        return "adversarial"
    return job + ":" + stage + ":" + str(len(seen) + 1)


name = logical_name()
plant.setdefault("calls", []).append(
    {"name": name, "job": job, "stage": stage, "argv": argv}
)
if "--detach" not in argv:
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stderr.write(
        "arguments: expected --detach; found a wait-and-hold dispatch; "
        "needed pass --detach\n"
    )
    raise SystemExit(3)
if name in plant.get("refuse_stages", []) or job in plant.get("refuse_jobs", []):
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stderr.write(
        "job: expected a mint; found a refusal; needed a dispatchable job\n"
    )
    raise SystemExit(3)

seq = plant.get("n", 0) + 1
plant["n"] = seq
rec_id = "20260904T120000Z-" + (stage or "stage") + "-" + (harness or "h") + "-aa" + f"{seq:02d}" + "32"
verdicts = plant.get("verdicts", {})
verdict = verdicts.get(name, "approve")
artifacts = {}
if name == "tests":
    if plant.get("empty_artifacts"):
        artifacts = {}
    else:
        artifacts = dict(plant.get("artifacts", {}))
if name == "check-tests" and plant.get("skeptic_md"):
    body = plant.get("skeptic_text") or "SKEPTIC_MD_BODY_aa32 the skeptic report"
    dest = REPO / "SKEPTIC.md"
    dest.write_text(body, encoding="utf-8")
    artifacts["skeptic"] = str(dest)
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        lane = conf.get("LANE_NAME") or "lanex"
        outp = Path(xdg) / lane / "dispatch" / rec_id / "out" / "SKEPTIC.md"
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(body, encoding="utf-8")
        artifacts["skeptic"] = str(outp)
mismatch = name in plant.get("model_mismatch_stages", [])
effort_mismatch = name in plant.get("effort_mismatch_stages", [])
requested = "wrong-model-aa32" if mismatch else model
effort_requested = "wrong-effort-aa32" if effort_mismatch else effort
ran = plant.get("ran_" + (harness or ""), (harness or "h") + "-ran-aa32")
note = None
if name == "code" or str(name).startswith("code-"):
    note = "IMPLEMENT_ENVELOPE_LINE_aa32 unique"
    artifacts["body"] = "IMPL_ENVELOPE_BODY_aa32 unique in envelope not in markers"
if plant.get("leak_implement_into") == name:
    note = "IMPLEMENT_ENVELOPE_LINE_aa32 unique"
notes = plant.get("notes") or {}
if name in notes:
    note = notes[name]
findings = []
if plant.get("leak_implement_into_findings") == name:
    findings.append({
        "severity": "p2",
        "where": "planted",
        "claim": "IMPL_ENVELOPE_BODY_aa32 unique in envelope not in markers",
        "reproduce": None,
    })
tripping = name in plant.get("trip_stages", [])
crashing = name in plant.get("crash_stages", [])
walling = name in plant.get("wait_wall_stages", [])
envelope = {
    "job": job,
    "status": "ok",
    "verdict": verdict,
    "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
    "findings": findings,
    "artifacts": artifacts,
    "spend": {"harness": harness, "total": 0, "out": 0, "runs": 0},
    "stamp": {"ref": "planted", "started": None, "ended": None},
    "note": note,
}
if name == "check-tests":
    if verdict == "changes" and not envelope["findings"]:
        envelope["findings"] = [{
            "severity": "p2",
            "where": "ops/conductor/tests/test_alpha.py:1",
            "claim": (
                "declared (ops/devlane/task/CONTRACT.md): "
                "the JSON envelope on stdout; "
                "practiced: Write the report to out/SKEPTIC.md; "
                "misses: a check-tests job that still writes a file"
            ),
            "reproduce": (
                "python3 -m unittest discover -s ops/conductor/tests "
                "-p test_alpha.py"
            ),
        }]
        envelope["counts"] = {"p1": 0, "p2": 1, "p3": 0, "opinions": 0}
    extra = plant.get("skeptic_envelope")
    if extra:
        envelope.update(extra)
record = {
    "id": rec_id,
    "lane": "dev",
    "stage": stage,
    "unit": flag("unit") or "lane/ladder-aa32",
    "lineage": {
        "branch": flag("branch") or "lane/ladder-aa32",
        "base_sha": "0" * 40,
    },
    "follows": [],
    "job": job,
    "role": "write" if job in ("author-tests", "implement") else "read",
    "dispatched_by": "stub",
    "at": {
        "launched": "2026-09-04T12:00:00Z",
        "closed": None,
    },
    "snapshot": {
        "mode": "whole",
        "ref_name": "HEAD",
        "ref_sha": "0" * 40,
        "behind_tip": 0,
        "root": "/tmp",
    },
    "harness": {
        "name": harness,
        "version": "0",
        "isolation": {
            "mechanism": "home",
            "observed": {"unresolved": "stub"},
        },
        "sandbox": "always-approve",
        "containment": "policy",
        "argv": [],
    },
    "model": {
        "requested": requested,
        "effort_requested": effort_requested,
        "effort_argv": ["--reasoning-effort", effort_requested or ""],
        "ran": ran,
        "read_from": None,
        "note": None,
    },
    "session": {"id": None, "stream": None, "stream_sha256_at_close": None},
    "brief": {
        "template": {"path": "jobs.json", "sha256": "a" * 64},
        "scope": "",
        "inputs": [],
        "sha256": "b" * 64,
        "bytes": 0,
    },
    "caps": {"timeout": 1800, "source": "jobs.json"},
    "overrides": [],
    "attempts": [],
    "result": None,
    "status": "launched",
}
dest = REPO / ".dev" / "records" / "dispatches" / (rec_id + ".json")
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
plant.setdefault("records", {})[rec_id] = {
    "name": name,
    "job": job,
    "path": str(dest),
}
plant.setdefault("envelopes", {})[rec_id] = envelope
plant["last_id"] = rec_id
plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
sys.stdout.write("record: " + rec_id + "\n")
sys.stdout.write("launcher-pid: " + str(os.getpid()) + "\n")
raise SystemExit(0)
