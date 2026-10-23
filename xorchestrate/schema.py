"""JSON Schema validation without fetching resources named by untrusted schemas."""
import json

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

from .model import OrchestrationError


def _no_retrieval(uri):
    raise NoSuchResource(ref=uri)


def check_schema(schema):
    try:
        json.dumps(schema, allow_nan=False)
        Draft202012Validator.check_schema(schema)
    except (SchemaError, TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("Invalid JSON Schema.", "invalid_schema") from exc


def validate(value, schema):
    check_schema(schema)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("Expected finite JSON values.") from exc
    try:
        Draft202012Validator(schema, registry=Registry(retrieve=_no_retrieval)).validate(value)
    except ValidationError as exc:
        # Do not echo values: tool arguments can contain secrets or large documents.
        path = "/".join(str(part) for part in exc.absolute_path)
        raise OrchestrationError(
            "Input does not satisfy the declared schema.",
            "invalid_arguments", {"path": path, "constraint": exc.validator},
        ) from exc
    except (Unresolvable, NoSuchResource) as exc:
        raise OrchestrationError("Schema reference is unavailable; external retrieval is disabled.", "invalid_schema") from exc
    except RecursionError as exc:
        raise OrchestrationError("Schema or input exceeds validation complexity.", "invalid_arguments") from exc
