"""Independent event scenarios for minimal PR validation work."""

METADATA_CHANGES = ({"title": {"from": "old"}}, {"body": {"from": "old"}},
                    {"title": {"from": "old"}, "body": {"from": "old"}})
VALIDATION_EVENTS = (("opened", {}), ("synchronize", {}), ("reopened", {}), ("edited", {}),
                     ("edited", {"base": {"ref": {"from": "old-base"}}}),
                     ("edited", {"title": {"from": "old"}, "base": {"ref": {"from": "old-base"}}}))
VALIDATION_JOBS = ("paths", "changes", "application-tests", "application", "verification",
                   "tmux-acceptance", "package", "architecture-status", "ci-result")
REQUIRED_NAMES = {"CI result", "Hexagonal architecture"}
