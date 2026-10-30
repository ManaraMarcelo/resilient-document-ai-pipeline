from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator


class ProviderError(RuntimeError):
    """An availability, transport, or rate-limit failure."""


class ContractError(ValueError):
    """A local configuration or response-contract failure."""


class StructuredProvider(Protocol):
    name: str

    def generate_json(self, *, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object or raise ProviderError."""


@dataclass(frozen=True)
class GatewayResult:
    payload: dict[str, Any]
    provider: str
    fallback_used: bool
    attempts: int


def validate_schema_contract(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object":
        raise ContractError("The top-level structured output must be an object")
    if schema.get("additionalProperties") is not False:
        raise ContractError("Strict output requires additionalProperties=false")
    Draft202012Validator.check_schema(schema)


class LlmGateway:
    def __init__(self, primary: StructuredProvider, secondary: StructuredProvider | None = None) -> None:
        self.primary = primary
        self.secondary = secondary

    def generate(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        max_attempts: int = 3,
    ) -> GatewayResult:
        validate_schema_contract(schema)
        validator = Draft202012Validator(schema)
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            for provider, fallback_used in self._providers():
                try:
                    payload = provider.generate_json(
                        prompt=prompt,
                        schema_name=schema_name,
                        schema=schema,
                    )
                except ProviderError as error:
                    last_error = error
                    continue  # technical failure: the secondary provider may help

                errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
                if errors:
                    last_error = ContractError("Provider returned JSON outside the output schema")
                    break  # retry the same logical request; do not accept malformed JSON

                return GatewayResult(
                    payload=payload,
                    provider=provider.name,
                    fallback_used=fallback_used,
                    attempts=attempt,
                )

        raise RuntimeError(f"Structured generation failed after {max_attempts} attempts: {last_error}")

    def _providers(self) -> tuple[tuple[StructuredProvider, bool], ...]:
        values: list[tuple[StructuredProvider, bool]] = [(self.primary, False)]
        if self.secondary is not None:
            values.append((self.secondary, True))
        return tuple(values)
