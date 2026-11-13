"""Operation challenges and witnessed holds for the existing native peers."""
import json
from pathlib import Path
from uuid import uuid4

from tests.hexagonal.support.operation_observation import ObservationError


class ProcessOperation:
    def __init__(self, directory, source, incarnation, operation):
        token = uuid4().hex
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.challenge = {"source": source, "incarnation": incarnation, "operation": operation,
                          "token": token, "receipt": str(self.directory / (token + ".operation.json"))}
        self.arguments = {"operation_observation": self.challenge.copy()}
        self.boundaries = {}

    def _read(self, path, stage):
        try:
            receipt = json.loads(Path(path).read_text())
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as error:
            raise ObservationError("invalid process activation receipt") from error
        if (not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in self.challenge.items())
                or receipt.get("stage") != stage or not isinstance(receipt.get("native_operation"), str)
                or not receipt["native_operation"]):
            raise ObservationError("process activation does not match its operation challenge")
        return receipt

    def activation(self):
        return self._read(self.challenge["receipt"], "accepted")

    def hold(self, stage):
        if stage in self.boundaries:
            raise ValueError("operation boundary already armed: " + stage)
        boundary = ProcessBoundary(self, stage)
        self.boundaries[stage] = boundary
        return boundary

    def close(self):
        for boundary in self.boundaries.values():
            boundary.close()


class ProcessBoundary:
    def __init__(self, operation, stage):
        self.operation, self.stage = operation, stage
        prefix = operation.directory / (operation.challenge["token"] + "." + stage)
        self.arguments = {stage + "_gate": str(prefix) + ".release",
                          stage + "_entered": str(prefix) + ".entered"}

    def activation(self):
        return self.operation._read(self.arguments[self.stage + "_entered"], self.stage)

    def entered(self):
        return self.activation() is not None

    def release(self):
        if self.activation() is None:
            raise ObservationError("cannot release an operation boundary before activation")
        self.close()

    def close(self):
        # Cleanup has a different purpose from releasing a tested boundary:
        # unblock even if the action failed before reaching the hold.
        Path(self.arguments[self.stage + "_gate"]).write_text(self.operation.challenge["token"])
