"""Run an operator-owned native conversation for later application attachment."""
import argparse
import json
import signal
import threading

from .adapters.bridge import SessionBridgeHost
from .composition import build, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--transport", choices=("unix", "fifo"), default="unix")
    parser.add_argument("--state", help="Private host state file; enables durable effect recovery and native resume after host restart.")
    parser.add_argument("--specification", default="{}", help="Native start specification as JSON.")
    parser.add_argument("--application-tools", action="store_true", help="Expose the active application's tool catalog to this native conversation.")
    args = parser.parse_args()
    config, base = load_config(args.config)
    specification = config.get("sessions", {}).get(args.driver)
    if specification is None:
        parser.error("The selected native driver must exist in the trusted configuration.")
    specification.setdefault("options", {})["application_tools"] = False
    # The host owns only the native driver. Application state and authorization
    # are provided by the attached application, not a second hidden runtime.
    host_config = {"store": {"adapter": "memory"}, "sessions": {args.driver: specification}}
    stopped = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    with build(host_config, base) as runtime:
        capabilities = runtime.application.describe() if args.application_tools else ()
        host = SessionBridgeHost(runtime.application.sessions[args.driver], args.endpoint,
            json.loads(args.specification), capabilities, {"transport": args.transport, "state_path": args.state})
        try:
            print(json.dumps({"ready": True, "endpoint": str(host.endpoint), "host_id": host.host_id,
                "native_id": host.backend["native_id"], "ownership": "operator"}), flush=True)
            stopped.wait()
        finally:
            host.close()


if __name__ == "__main__":
    main()
