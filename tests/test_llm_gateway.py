import unittest
from typing import Any

from llm_gateway import ContractError, LlmGateway, ProviderError, validate_schema_contract

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["label"],
    "properties": {"label": {"type": "string", "enum": ["A", "B"]}},
}


class FakeProvider:
    """Returns (or raises) queued responses in order."""

    def __init__(self, name: str, responses: list[Any]) -> None:
        self.name = name
        self.responses = list(responses)
        self.calls = 0

    def generate_json(self, *, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SchemaContractTests(unittest.TestCase):
    def test_non_object_schema_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            validate_schema_contract({"type": "array", "additionalProperties": False})

    def test_non_strict_schema_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            validate_schema_contract({"type": "object"})

    def test_strict_schema_is_accepted(self) -> None:
        validate_schema_contract(SCHEMA)


class LlmGatewayTests(unittest.TestCase):
    def test_primary_success(self) -> None:
        primary = FakeProvider("primary", [{"label": "A"}])
        result = LlmGateway(primary).generate(prompt="p", schema_name="s", schema=SCHEMA)
        self.assertEqual(result.payload, {"label": "A"})
        self.assertEqual(result.provider, "primary")
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.attempts, 1)

    def test_technical_failure_falls_back_to_secondary(self) -> None:
        primary = FakeProvider("primary", [ProviderError("timeout")])
        secondary = FakeProvider("secondary", [{"label": "B"}])
        result = LlmGateway(primary, secondary).generate(prompt="p", schema_name="s", schema=SCHEMA)
        self.assertEqual(result.provider, "secondary")
        self.assertTrue(result.fallback_used)

    def test_schema_violation_retries_without_switching_provider(self) -> None:
        primary = FakeProvider("primary", [{"label": "INVALID"}, {"label": "A"}])
        secondary = FakeProvider("secondary", [])
        result = LlmGateway(primary, secondary).generate(prompt="p", schema_name="s", schema=SCHEMA)
        self.assertEqual(result.provider, "primary")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(secondary.calls, 0)

    def test_exhausted_attempts_raise(self) -> None:
        primary = FakeProvider("primary", [ProviderError("down")] * 2)
        with self.assertRaises(RuntimeError):
            LlmGateway(primary).generate(prompt="p", schema_name="s", schema=SCHEMA, max_attempts=2)
        self.assertEqual(primary.calls, 2)

    def test_invalid_contract_fails_before_any_provider_call(self) -> None:
        primary = FakeProvider("primary", [])
        with self.assertRaises(ContractError):
            LlmGateway(primary).generate(prompt="p", schema_name="s", schema={"type": "object"})
        self.assertEqual(primary.calls, 0)


if __name__ == "__main__":
    unittest.main()

