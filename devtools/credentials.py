"""Read operator-owned credentials at use time without changing process environments."""

from __future__ import annotations

import os
import stat

MAX_CREDENTIAL_BYTES = 4096


class CredentialUnavailable(ValueError):
    """The configured credential is absent; malformed sources remain configuration errors."""


def read_credential(reference):
    """Return a key from a validated env/file reference; never include its value in errors.

    File contents are read for each request so an operator can rotate a key without
    restarting the service. Inspection and policy loading must not call this function.
    """
    if not isinstance(reference, dict) or set(reference) not in ({"env"}, {"file"}):
        raise ValueError("A credential needs exactly one environment or file reference.")
    source = next(iter(reference))
    location = reference[source]
    if not isinstance(location, str) or not location or "\0" in location:
        raise ValueError("The credential reference must be nonempty text.")
    if source == "env":
        value = os.environ.get(location, "")
        if not value:
            raise CredentialUnavailable("The operator must set " + location + " for the dev-lane service.")
    else:
        descriptor = None
        try:
            descriptor = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("The credential source must be a regular file.")
                if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                    raise ValueError("The credential file must be private to its owner (mode 0600 or 0400).")
                if info.st_size > MAX_CREDENTIAL_BYTES:
                    raise ValueError("The credential exceeds its size limit.")
                raw = stream.read(MAX_CREDENTIAL_BYTES + 1)
                if len(raw) > MAX_CREDENTIAL_BYTES:
                    raise ValueError("The credential exceeds its size limit.")
            value = raw.decode("ascii").rstrip("\r\n")
            if not value:
                raise CredentialUnavailable("The configured credential file is empty.")
        except FileNotFoundError:
            raise CredentialUnavailable("The operator must supply the configured credential file.") from None
        except (OSError, UnicodeError):
            raise ValueError("The credential file must be readable, regular and contain an ASCII key.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
    if len(value) > MAX_CREDENTIAL_BYTES or any(not 33 <= ord(char) <= 126 for char in value):
        raise ValueError("The credential must be one bounded key without whitespace or control characters.")
    return value


def credential_values(connection):
    """Supply exact values for redaction, without treating a missing key as available."""
    try:
        return (read_credential(connection["credential"]),)
    except CredentialUnavailable:
        return ()


def configured_values(directory):
    """Refresh all operator-owned keys for capture, including role/job overrides."""
    from .configuration import credential_references

    return tuple(value for reference in credential_references(directory)
                 for value in credential_values({"credential": reference}))


def redact(value, connection):
    from .telemetry import clean

    return clean(value, secrets=credential_values(connection))
