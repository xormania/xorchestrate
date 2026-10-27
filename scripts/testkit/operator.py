"""Pure managed-run lifecycle decisions and authoritative result projection."""

from copy import deepcopy

FINISHED = frozenset({"finished", "incomplete"})


def decide(state, event, **observations):
    """Return a state change; adapters own publication, processes and archives."""
    phase = state.get("phase", "allocated")
    if event == "ready":
        if phase not in {"allocated", "allocating"}:
            raise ValueError("Only an allocated run can become ready")
        return {"phase": "running", "ready": True, "proof": "pending", "outcome": "pending"}
    if event == "stop":
        if phase in FINISHED or phase == "finalizing":
            return {}
        return {"phase": "cancelling", "reason": state.get("reason") or observations["reason"]}
    if event == "worker_finished":
        report = observations.get("report")
        proof = report.get("status", "incomplete") if isinstance(report, dict) else "incomplete"
        selected = report.get("selected_check_ids", []) if isinstance(report, dict) else []
        checks = report.get("checks", []) if isinstance(report, dict) else []
        if proof == "passed" and (not selected or [row.get("name") for row in checks] != selected):
            proof = "incomplete"
        if observations.get("exit_status") != 0 and proof == "passed":
            proof = "incomplete"
        return {"phase": "finalizing", "proof": proof, "worker_state": "exited",
                "worker_exit": observations.get("exit_status"), "cleanup": observations["cleanup"]}
    if event == "finalized":
        if phase != "finalizing":
            raise ValueError("Run finalization requires an exited worker")
        collection = observations["collection"]
        cleanup = state.get("cleanup", {})
        clean = cleanup.get("verified") is True and not cleanup.get("forced")
        collected = collection.get("status") == "completed" and bool(collection.get("archive"))
        passed = state.get("proof") == "passed" and clean and collected and not state.get("reason")
        return {"phase": "incomplete" if state.get("proof") == "incomplete" else "finished",
                "collection": collection, "outcome": "passed" if passed else "failed", "ready": False}
    raise ValueError("Unknown lifecycle event: " + event)


def project(state, *, owner_alive, worker_alive):
    """A successful query is independent of the saved proof/operational result."""
    result = deepcopy(state)
    result["observed"] = {"supervisor_alive": bool(owner_alive), "worker_alive": bool(worker_alive)}
    if state.get("phase") not in FINISHED and not owner_alive:
        result.update(saved_phase=state.get("phase"), phase="incomplete", outcome="failed")
        result["observation_error"] = "Supervisor is not alive; orderly finalization is unverified"
    return result
