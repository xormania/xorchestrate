"""Private local MCP/hook gateway for native harness subprocesses.

The executable half uses only the standard library, so a configured harness can
start it by absolute filename without depending on its Python import path.
A descriptor is a local capability: its owner must keep its directory private.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hmac
import json
import os
import secrets
import socket
import socketserver
import sys
import tempfile
import threading
import uuid
from pathlib import Path


def _decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    json.dumps(value, allow_nan=False)
    return value


def _encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class _UnixServer(_Server):
    address_family = socket.AF_UNIX


class LocalGateway:
    """Bounded, authenticated request server used by native MCP/hook children."""

    def __init__(self, handler, options=None):
        options = options or {}
        self.maximum = options.get("max_frame_bytes", 4 * 1024 * 1024)
        self.timeout = options.get("gateway_timeout", 300)
        workers = options.get("gateway_workers", 16)
        if type(self.maximum) is not int or self.maximum <= 0 or type(workers) is not int or workers < 2:
            raise ValueError("Gateway frame/worker bounds are invalid")
        self.directory = tempfile.TemporaryDirectory(prefix="xor-native-")
        os.chmod(self.directory.name, 0o700)
        self.token = secrets.token_hex(32)
        transport = options.get("gateway_transport", "tcp")
        if transport not in {"tcp", "unix"}:
            raise ValueError("Gateway transport must be tcp or unix")
        self.socket_path = str(Path(self.directory.name) / "gateway.sock")
        self.descriptor_path = Path(self.directory.name) / "descriptor.json"
        gateway = self

        class Request(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(gateway.timeout)
                try:
                    raw = self.rfile.readline(gateway.maximum + 1)
                    if not raw.endswith(b"\n") or len(raw) > gateway.maximum:
                        raise ValueError("Gateway input exceeds frame bound")
                    packet = _decode(raw)
                    if not isinstance(packet, dict) or not isinstance(packet.get("token"), str) or not hmac.compare_digest(packet["token"], gateway.token):
                        raise ValueError("Gateway authentication failed")
                    result = handler(packet.get("action"), packet.get("payload", {}))
                    response = {"result": result}
                except Exception as error:
                    # Do not relay arbitrary exception strings (which can contain credentials).
                    response = {"error": {"code": getattr(error, "code", "gateway_error"),
                                           "message": "Native gateway request failed"}}
                raw = _encode(response)
                if len(raw) > gateway.maximum:
                    raw = _encode({"error": {"code": "frame_limit", "message": "Gateway result exceeds bound"}})
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass

        self.server = (_UnixServer(self.socket_path, Request) if transport == "unix"
                       else _Server(("127.0.0.1", 0), Request))
        self.descriptor_path.write_bytes(_encode({"transport": transport, "address": self.server.server_address,
                                                "token": self.token, "maximum": self.maximum, "timeout": self.timeout}))
        os.chmod(self.descriptor_path, 0o600)
        self.server.capacity = threading.BoundedSemaphore(workers)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def mcp_spec(self, channel=False):
        args = [str(Path(__file__).resolve()), "mcp", str(self.descriptor_path)]
        if channel:
            args.append("--channel")
        return {"command": sys.executable, "args": args}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.directory.cleanup()


def request(descriptor, action, payload=None):
    config = _decode(Path(descriptor).read_bytes())
    packet = _encode({"token": config["token"], "action": action, "payload": payload or {}})
    maximum = config["maximum"]
    if len(packet) > maximum:
        raise ValueError("Gateway input exceeds bound")
    family = socket.AF_UNIX if config["transport"] == "unix" else socket.AF_INET
    address = config["address"] if family == socket.AF_UNIX else tuple(config["address"])
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(config["timeout"])
        connection.connect(address)
        connection.sendall(packet)
        with connection.makefile("rb") as stream:
            raw = stream.readline(maximum + 1)
    if len(raw) > maximum or not raw.endswith(b"\n"):
        raise ValueError("Invalid gateway response")
    result = _decode(raw)
    if "error" in result:
        raise RuntimeError(result["error"]["code"])
    return result["result"]


def run_mcp(descriptor, channel=False):
    config = _decode(Path(descriptor).read_bytes())
    output_lock = threading.Lock()
    initialized = threading.Event()
    stopped = threading.Event()
    capacity = threading.BoundedSemaphore(8)
    connection_id = uuid.uuid4().hex

    def write(packet):
        raw = _encode({"jsonrpc": "2.0", **packet})
        if len(raw) > config["maximum"]:
            raise ValueError("MCP output exceeds bound")
        with output_lock:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    def channels():
        while not stopped.is_set() and not initialized.wait(0.1):
            pass
        while not stopped.is_set():
            try:
                delivery = request(descriptor, "channel/next")
                if delivery is None:
                    continue
                write({"method": "notifications/claude/channel", "params": delivery["params"]})
                request(descriptor, "channel/sent", {"delivery_id": delivery["delivery_id"]})
            except Exception:
                stopped.set()

    def handle(packet):
        ident, method = packet.get("id"), packet.get("method")
        try:
            if method == "notifications/initialized":
                request(descriptor, "initialized", {"channel": channel,
                    "native_id": os.environ.get("CLAUDE_CODE_SESSION_ID")})
                initialized.set()
                return
            if ident is None:
                return
            if method == "initialize":
                caps = {"tools": {}}
                if channel:
                    caps["experimental"] = {"claude/channel": {}}
                result = {"protocolVersion": "2025-06-18", "capabilities": caps,
                          "serverInfo": {"name": "xorchestrate", "version": "0.2.0"}}
            elif method == "ping":
                result = {}
            elif method in {"tools/list", "tools/call"}:
                value = request(descriptor, method, {**packet.get("params", {}), "mcp_request_id": ident, "mcp_connection_id": connection_id})
                result = value if method == "tools/list" else {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}], "structuredContent": value}
            else:
                write({"id": ident, "error": {"code": -32601, "message": "Unsupported method"}})
                return
            write({"id": ident, "result": result})
        except Exception:
            if ident is not None:
                write({"id": ident, "error": {"code": -32603, "message": "Native gateway request failed"}})

    def call(packet):
        try:
            handle(packet)
        finally:
            capacity.release()

    channel_thread = threading.Thread(target=channels, daemon=True)
    if channel:
        channel_thread.start()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        while not stopped.is_set():
            line = sys.stdin.buffer.readline(config["maximum"] + 1)
            if not line:
                break
            if len(line) > config["maximum"] or not line.endswith(b"\n"):
                return 2
            try:
                packet = _decode(line)
                if not isinstance(packet, dict):
                    raise ValueError("Expected object")
            except ValueError:
                write({"id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC"}})
                continue
            if packet.get("method") == "tools/call":
                if capacity.acquire(blocking=False):
                    pool.submit(call, packet)
                else:
                    write({"id": packet.get("id"), "error": {"code": -32000, "message": "Callback capacity exceeded"}})
            else:
                handle(packet)
    finally:
        stopped.set()
        pool.shutdown(wait=False, cancel_futures=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("mcp", "hook"))
    parser.add_argument("descriptor")
    parser.add_argument("--channel", action="store_true")
    args = parser.parse_args()
    if args.mode == "mcp":
        return run_mcp(args.descriptor, args.channel)
    config = _decode(Path(args.descriptor).read_bytes())
    raw = sys.stdin.buffer.read(config["maximum"] + 1)
    if len(raw) > config["maximum"]:
        return 2
    try:
        response = request(args.descriptor, "hook", _decode(raw))
        if response:
            print(json.dumps(response, allow_nan=False))
        return 0
    except Exception:
        print("Xorchestrate could not verify the native hook.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
