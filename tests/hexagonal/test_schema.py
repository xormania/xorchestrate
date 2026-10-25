import unittest
from unittest.mock import patch

from xorchestrate.model import OrchestrationError
from xorchestrate.schema import check_schema, validate


class SchemaTests(unittest.TestCase):
    def assert_error(self, code, value, schema):
        with self.assertRaises(OrchestrationError) as caught:
            validate(value, schema)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_local_references_and_composition_validate_without_network(self):
        schema = {"$defs": {"name": {"type": "string", "minLength": 1}},
                  "type": "object", "required": ["name"], "additionalProperties": False,
                  "properties": {"name": {"$ref": "#/$defs/name"},
                                 "choice": {"oneOf": [{"type": "integer"}, {"type": "null"}]}}}
        with patch("socket.create_connection") as network:
            validate({"name": "declared", "choice": None}, schema)
            validate({"name": "declared", "choice": 4}, schema)
            error = self.assert_error("invalid_arguments", {"name": ""}, schema)
            self.assertEqual(error.details, {"path": "name", "constraint": "minLength"})
            network.assert_not_called()

    def test_external_references_cannot_fetch_http_or_local_files(self):
        for reference in ("https://example.invalid/private-schema.json", "http://127.0.0.1:12345/metadata", "file:///etc/passwd"):
            with self.subTest(reference=reference), patch("socket.create_connection") as network, patch("urllib.request.urlopen") as opener:
                self.assert_error("invalid_schema", {}, {"$ref": reference})
                network.assert_not_called()
                opener.assert_not_called()

    def test_missing_local_reference_is_a_schema_error(self):
        self.assert_error("invalid_schema", {}, {"$ref": "#/$defs/missing"})

    def test_required_and_additional_fields_are_checked_without_echoing_values(self):
        schema = {"type": "object", "required": ["goal"], "additionalProperties": False,
                  "properties": {"goal": {"type": "string"}}}
        self.assertEqual(self.assert_error("invalid_arguments", {}, schema).details["constraint"], "required")
        error = self.assert_error("invalid_arguments", {"goal": "work", "secret": "DO-NOT-ECHO-THIS"}, schema)
        self.assertEqual(error.details["constraint"], "additionalProperties")
        self.assertNotIn("DO-NOT-ECHO-THIS", str(error))
        self.assertNotIn("DO-NOT-ECHO-THIS", repr(error.details))

    def test_boolean_schemas_and_invalid_schema_are_distinct(self):
        validate({"anything": True}, True)
        self.assert_error("invalid_arguments", {}, False)
        for schema in ({"type": "not-a-type"}, {"required": "bad"}, {"minimum": float("nan")}, None):
            with self.subTest(schema=schema), self.assertRaises(OrchestrationError) as caught:
                check_schema(schema)
            self.assertEqual(caught.exception.code, "invalid_schema")

    def test_nonfinite_and_non_json_inputs_are_rejected_even_by_open_schema(self):
        for value in (float("nan"), float("inf"), {"nested": [float("-inf")]}, {"value": object()}):
            with self.subTest(value=value):
                self.assert_error("invalid_request", value, {})


if __name__ == "__main__":
    unittest.main()
