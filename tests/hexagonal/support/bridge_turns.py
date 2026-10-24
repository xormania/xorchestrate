"""Public bridge-port fixture for asynchronous call/turn ownership contracts."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from xorchestrate.adapters.bridge import BridgeSessionDriver, SessionBridgeHost
from xorchestrate.model import Capability, OrchestrationError


class AsynchronousNative:
    """A minimal interchangeable native port; emits no vendor wire protocol."""
    def bind_application_tools(self, capabilities, callback):
        self.callback = callback

    def bind_observer(self, observer):
        self.observer = observer

    def start(self, specification):
        self.backend = {"native_id": "controlled-native", "incarnation": "native-incarnation", "protocol": "custom"}
        return deepcopy(self.backend)

    def emit(self, kind, **fields):
        self.observer({}, {**self.backend, "kind": kind, **fields})

    def poll(self, backend):
        return []

    def close(self):
        pass


class BridgeTurnFixture:
    def __init__(self, stack, native, host, transport, turn_field):
        self.stack, self.native, self.host, self.transport = stack, native, host, transport
        self.turn_field = turn_field
        self.observed = []
        self.replace_owner()

    def replace_owner(self):
        self.client = BridgeSessionDriver({"endpoint": str(self.host.endpoint), "transport": self.transport, "timeout": 5})
        self.stack.callback(self.client.close)
        self.client.bind_application_tools([Capability("echo", "controlled callback")], lambda *args: {"owner": "current-owner"})
        self.client.bind_observer(lambda context, observation: self.observed.append(deepcopy(observation)))
        self.receipt = self.client.attach({"context": {"actor": "owner-" + str(len(self.observed))}})

    def observe_call(self, call, turn, *, identity="current"):
        fields = {"call_id": call, self.turn_field: turn, "action": "echo", "arguments": {}}
        if identity == "wrong-native":
            fields["native_id"] = "contradictory-native"
        elif identity == "wrong-incarnation":
            fields["incarnation"] = "contradictory-incarnation"
        self.native.emit("native_tool", **fields)

    def invoke_callback(self, call):
        try:
            return self.native.callback("echo", {}, {**self.native.backend, "call_id": call}, call)["owner"]
        except OrchestrationError as error:
            return error.code

    def complete(self, turn):
        before = len(self.observed)
        self.native.emit("turn_completed", **{self.turn_field: turn})
        self.client.poll(self.receipt)
        return any(row.get("kind") == "turn_completed" and row.get(self.turn_field) == turn
                   and row.get("incarnation") == self.receipt["incarnation"] for row in self.observed[before:])


@contextmanager
def bridge_turn_fixture(directory, transport, turn_field):
    with ExitStack() as stack:
        # Evidence roots can exceed AF_UNIX's path limit. Only the transport
        # endpoint uses this short private directory; test evidence stays put.
        endpoint_root = Path(stack.enter_context(TemporaryDirectory(prefix="xor-turn-")))
        native = AsynchronousNative()
        host = SessionBridgeHost(native, endpoint_root / "host", capabilities=[Capability("echo", "controlled callback")],
                                 options={"transport": transport})
        stack.callback(host.close)
        yield BridgeTurnFixture(stack, native, host, transport, turn_field)
