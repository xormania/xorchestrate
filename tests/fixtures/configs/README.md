# Configuration format fixtures

One project configuration per configuration format version, as a project would
track it in `.xorchestrate/config.json`, plus one written before configurations
declared a version. The release gate in `tests/hexagonal/test_config_versioning.py`
checks that every supported version loads, that `xorchestrate migrate-config`
rewrites each older one (and the unversioned one) to the current fixture byte for
byte, and that a configuration one version ahead is refused.

| File | Format version |
| --- | --- |
| `config-unversioned.json` | none (read as version 1) |
| `config-v1.json` | 1 |

When a release bumps the configuration format version, add `config-vN.json` for
the new version, written as `migrate-config --write` writes it (two-space indented
JSON with sorted keys and a final newline), and keep the earlier files unchanged:
they are the inputs of each rewrite step.
