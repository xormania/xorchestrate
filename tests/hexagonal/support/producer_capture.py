"""Concrete HTTP/process fixtures for the producer-capture contracts."""

import base64
import copy
import inspect
import json
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.hexagonal.contracts.producer_capture import assert_protected_contract, retained_bytes_contract
from xorchestrate.adapters.evaluation import TypeSafeEvaluator
from xorchestrate.adapters.jsonrpc import JsonLineProcess, run_json_process


def retained_bytes(event):
    content = base64.b64decode(event["payload"]["content"], validate=True)
    return retained_bytes_contract(event, content)


def assert_protected(events, secret):
    retained_contents = [retained_bytes(event) for event in events if event.get("payload", {}).get("encoding") == "base64"]
    assert_protected_contract(events, secret, retained_contents)


class Recorder:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, event):
        with self.lock:
            self.events.append(copy.deepcopy(event))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.events)


def bind_if_supported(adapter, observer):
    # The baseline still executes its real behavior. Missing instrumentation
    # fails the capture assertions, rather than an import or API-shape check.
    bind = getattr(adapter, "bind_telemetry", None)
    if callable(bind):
        bind(observer)
    return adapter


def evaluator(options, observer):
    return bind_if_supported(TypeSafeEvaluator(options), observer)


def one_shot(options, payload, observer, context=None):
    extra = {}
    if "telemetry_observer" in inspect.signature(run_json_process).parameters:
        extra = {"telemetry_observer": observer, "telemetry_context": context or {}}
    return run_json_process(options, payload, **extra)


def rpc(options, observer, context=None):
    extra = {}
    if "telemetry_observer" in inspect.signature(JsonLineProcess).parameters:
        extra = {"telemetry_observer": observer, "telemetry_context": context or {}}
    return JsonLineProcess(options, **extra)


def process_options(mode, **extra):
    return {
        "command": [sys.executable, "-u", str(Path(__file__).parents[1] / "fixtures" / "telemetry_peer.py"), mode],
        "timeout": 2,
        "shutdown_timeout": 0.02,
        **extra,
    }


class HttpFixture:
    def __init__(self, body, status=200, delay=0, chunk_delay=None):
        self.body, self.status, self.delay, self.chunk_delay = body, status, delay, chunk_delay
        self.requests = []
        self.stop = threading.Event()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                fixture.requests.append((dict(self.headers), payload))
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(fixture.body)))
                self.end_headers()
                if fixture.delay:
                    time.sleep(fixture.delay)
                try:
                    if fixture.chunk_delay is None:
                        self.wfile.write(fixture.body)
                    else:
                        for byte in fixture.body:
                            if fixture.stop.wait(fixture.chunk_delay):
                                break
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def options(self, **extra):
        return {
            "endpoint": f"http://127.0.0.1:{self.server.server_port}/evaluate",
            "allow_http": True,
            "model": "fixture-model",
            "criteria": {"ready": "Evidence establishes readiness", "uncertain": "Evidence is incomplete"},
            "timeout": 1,
            **extra,
        }


def response_body(*, usage=True):
    payload = {
        "model": "fixture-model",
        "answers": {"assessment": {"type": "choice", "choice": "ready", "confidence": .9,
                                      "probabilities": {"ready": .9, "uncertain": .1}}},
    }
    if usage:
        payload["usage"] = {"input_tokens": 0, "output_tokens": 2}
    return json.dumps(payload, ensure_ascii=False).encode()


@contextmanager
def failing_observer():
    calls = []

    def observe(event):
        calls.append(copy.deepcopy(event))
        raise RuntimeError("private collector failure")

    yield observe, calls
