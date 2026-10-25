"""Pure values shared by verification ports, evidence projection and invariants."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class VerificationFailure(Exception):
    def __init__(self, assertion):
        super().__init__(assertion)
        self.assertion = assertion


def require(value, assertion):
    if not value:
        raise VerificationFailure(assertion)
