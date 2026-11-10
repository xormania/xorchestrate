# Telemetry compatibility fixtures

These files were written by the standalone collector that Xorchestrate used before
the collector moved into `xorchestrate/adapters/telemetry_collector/`. Tests copy
them into a private temporary directory and check that the in-tree collector still
opens the corpus and verifies and restores the bundle. Do not regenerate them to
make a test pass: they record the format that existing data already has.

| Path | Content |
| --- | --- |
| `corpus/events.sqlite3` | `agent-telemetry-v1` corpus (`user_version` 1, WAL mode), 4 events: `application.requested`/`application.completed` for two `create_work` calls; project `fixture-project`, dataset `fixture-dataset`, collector `fixture-collector`, producer `fixture-producer` |
| `corpus/artifacts/` | 3 `filesystem` content objects for payloads above the 256-byte artifact threshold |
| `bundle/` | Completed `xorchestrate-telemetry-bundle-v1` snapshot of that corpus, bundle id `fc12e9bbb5f099641bd64e724b2dd3d81118d50b14ea0b2544568edc71dad901`; its manifest records `software.agent_telemetry` `0.1.0` |

## How they were produced

- Collector: `xormania/cce` at `84229bb7497547d2db91d02a4633ccb3867f740b`, package
  `tools/telemetry/agent_telemetry` (version `0.1.0`).
- Bridge and snapshot writer: Xorchestrate `246206a`, the last revision that used
  that package, extracted with `git archive 246206a xorchestrate` into a scratch
  directory named `master`.
- Python 3.13.18 with SQLite 3.53.1, 2026-11-10.

The script below ran from a scratch directory outside the repository, with only
those two source trees placed ahead of the interpreter's path:

```bash
python -I -c "import sys; sys.path[:0]=['<scratch>/master', '<cce-84229bb>/tools/telemetry']; \
  sys.argv=['generate', '<repository>/tests/fixtures/telemetry']; exec(open('generate_fixtures.py').read())"
```

`generate_fixtures.py`:

```python
import json
import shutil
import sys
import tempfile
from pathlib import Path

import agent_telemetry
from agent_telemetry.sqlite import SQLiteRepository
import xorchestrate.adapters.telemetry as bridge
from xorchestrate.composition import build

assert bridge.__file__.find("/master/") >= 0, bridge.__file__  # the extracted 246206a tree
output = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)
    config = {"store": {"adapter": "memory"}, "telemetry": {"adapter": "local", "options": {
        "runtime_root": "runtime", "project_id": "fixture-project", "dataset_id": "fixture-dataset",
        "collector_id": "fixture-collector", "producer_id": "fixture-producer", "artifact_threshold_bytes": 256}}}
    with build(config, root) as runtime:
        app = runtime.application
        small = app.call("create_work", {"goal": "compatibility fixture: small payload"})
        large = app.call("create_work", {"goal": "compatibility fixture: artifact payload " + "x" * 600,
                                         "context": {"api_key": "fixture-not-a-secret"}})
        receipt = app.call("telemetry_snapshot", {"destination": "bundle"})
        status = app.telemetry.status()
    assert receipt["status"] == "complete" and receipt["artifact_count"] >= 1, receipt
    corpus = output / "corpus"
    bundle = output / "bundle"
    for path in (corpus, bundle):
        if path.exists():
            shutil.rmtree(path)
    corpus.mkdir(parents=True)
    runtime_root = root / "runtime"
    # Read-only inspection connections leave the WAL in place; the last writable
    # close checkpoints it so the committed corpus is one self-contained file.
    SQLiteRepository(runtime_root / "events.sqlite3").close()
    for sidecar in ("-wal", "-shm", "-journal"):
        assert not (runtime_root / ("events.sqlite3" + sidecar)).exists(), sidecar
    shutil.copy2(runtime_root / "events.sqlite3", corpus / "events.sqlite3")
    shutil.copytree(runtime_root / "artifacts", corpus / "artifacts")
    shutil.copytree(runtime_root / "exports" / "bundle", bundle)
    summary = {"collector_version": agent_telemetry.__version__, "event_count": status["event_count"],
               "work_ids": [small["work"]["id"], large["work"]["id"]], "bundle_id": receipt["bundle_id"],
               "artifact_count": receipt["artifact_count"]}
    print(json.dumps(summary, indent=2))
```

The `api_key` value is a placeholder; the bridge's credential protection removed
it, and no file here contains it. The `.sqlite3` files are listed as exceptions in
`.gitignore`.
