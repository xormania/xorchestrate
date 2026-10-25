"""Normalized evidence and candidate fixtures for independent checker tests.

The normalized chain is constructed directly, not projected by the normalizer
under test. Raw observation fixtures live in raw_evidence.py.
"""
import subprocess


def repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "fixture"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "fixture@example.invalid"], check=True)
    (path / "module.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return path



def proof_chain():
    sessions = {p: {"native_id": p + "-native", "session_id": p + "-app", "incarnation": p + "-epoch"}
                for p in ("codex", "grok")}
    case = {"id": "case", "source": "codex", "target": "grok", "kind": "exchange"}
    events = []
    def add(event, peer, **data):
        value = {"seq": len(events) + 1, "event": event, "peer": peer, **sessions[peer], **data}
        events.append(value)
        return value
    def callback(peer, tool, args, event, **data):
        call_id = "call-" + str(len(events))
        add("native_tool", peer, tool=tool, arguments=args, call_id=call_id,
            origin={"codex": "codex/item/tool/call", "grok": "grok/session/update"}[peer])
        add("tool_called", peer, tool=tool, arguments=args, call_id=call_id)
        add(event, peer, tool=tool, arguments=args, call_id=call_id, **data)
        add("tool_completed", peer, tool=tool, arguments=args, call_id=call_id)
    challenge = {"id": "challenge", "to": "grok", "kind": "challenge", "case_id": "case", "nonce": "nonce", "memory": ""}
    callback("codex", "send_message", challenge, "queued", message=challenge)
    add("attempt", "grok", message_id="challenge", attempt_id="attempt-1", recipient="grok",
        attempt_sequence=1, status="submitted", origin="adapter", submission={"submitted": True})
    add("submitted", "grok", message_id="challenge", attempt_id="attempt-1", state="submitted")
    reply = {"id": "reply", "to": "codex", "kind": "reply", "case_id": "case", "nonce": "nonce", "memory": "private"}
    callback("grok", "send_message", reply, "queued", message=reply)
    add("attempt", "codex", message_id="reply", attempt_id="attempt-2", recipient="codex",
        attempt_sequence=1, status="submitted", origin="adapter", submission={"submitted": True})
    add("submitted", "codex", message_id="reply", attempt_id="attempt-2", state="submitted")
    receipt = {"phase": "received", "case_id": "case", "nonce": "nonce", "memory": "private", "peer": "grok"}
    callback("codex", "record_result", receipt, "report", report=receipt)
    return events, sessions, case

