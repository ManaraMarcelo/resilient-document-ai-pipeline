from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class ExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    PENDING_CONTEXT = "PENDING_CONTEXT"
    FAILED = "FAILED"


TERMINAL_STATUSES = {
    ExecutionStatus.COMPLETED,
    ExecutionStatus.REVIEW_REQUIRED,
    ExecutionStatus.OUT_OF_SCOPE,
    ExecutionStatus.PENDING_CONTEXT,
    ExecutionStatus.FAILED,
}


@dataclass(frozen=True)
class JobInput:
    job_id: int
    document_refs: tuple[str, ...]
    context: dict[str, Any]


@dataclass(frozen=True)
class ModelResult:
    provider: str
    model: str | None
    fallback_used: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: UUID
    status: ExecutionStatus
    attempts: int
    duration_ms: int
    model_result: ModelResult | None = None
    document_hashes: dict[str, str] = field(default_factory=dict)
    operational_output: dict[str, Any] = field(default_factory=dict)
    review_reasons: tuple[str, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status == ExecutionStatus.RUNNING:
            raise ValueError("ExecutionResult must be terminal")
        if self.attempts < 0 or self.duration_ms < 0:
            raise ValueError("attempts and duration_ms must be non-negative")
