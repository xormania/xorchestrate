"""Run an operator-owned native conversation for later application attachment."""
import argparse
import json
import os
import signal
import threading
from copy import deepcopy

from .adapters.bridge_host import SessionBridgeHost
from .composition import build, load_config


def host_configuration(config, driver):
    """The host's own configuration: the selected driver under the operator's format version.

    The host owns only the native driver. Application state and authorization
    are provided by the attached application, not a second hidden runtime.
    """
    specification = deepcopy(config["sessions"][driver])
    specification.setdefault("options", {})["application_tools"] = False
    host_config = {"store": {"adapter": "memory"}, "sessions": {driver: specification}}
    # Carry the format version so the host refuses what composition refuses.
    for section in ("version", "state_engine"):
        if section in config:
            host_config[section] = config[section]
    return host_config


def _specification(args):
    """The native start specification: from the private file when given, else the command-line JSON."""
    if args.specification_file is None:
        return json.loads(args.specification)
    descriptor = os.open(args.specification_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise SystemExit("The specification file must be private to this user (mode 0600).")
        return json.loads(stream.read(1_048_576))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--transport", choices=("unix", "fifo"), default="unix")
    parser.add_argument("--state", help="Private host state file; enables durable effect recovery and native resume after host restart.")
    parser.add_argument("--specification", default="{}", help="Native start specification as JSON.")
    parser.add_argument("--specification-file",
                        help="A private file (this user's, mode 0600) holding the native start specification as "
                             "JSON; keeps it off the command line. Takes precedence over --specification.")
    parser.add_argument("--application-tools", action="store_true", help="Expose the active application's tool catalog to this native conversation.")
    args = parser.parse_args()
    config, base = load_config(args.config)
    specification = config.get("sessions", {}).get(args.driver)
    if specification is None:
        parser.error("The selected native driver must exist in the trusted configuration.")
    host_config = host_configuration(config, args.driver)
    stopped = threading.Event()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: stopped.set())
    with build(host_config, base) as runtime:
        capabilities = runtime.application.describe() if args.application_tools else ()
        host = SessionBridgeHost(runtime.application.sessions[args.driver], args.endpoint,
            _specification(args), capabilities, {"transport": args.transport, "state_path": args.state},
            state_engine=runtime.application.state_engine)
        try:
            print(json.dumps({"ready": True, "endpoint": str(host.endpoint), "host_id": host.host_id,
                "native_id": host.backend["native_id"], "ownership": "operator"}), flush=True)
            stopped.wait()
        finally:
            host.close()


if __name__ == "__main__":
    main()
