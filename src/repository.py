from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from models import TERMINAL_STATUSES, ExecutionResult, JobInput


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_jsonb(value: Any) -> str | None:
    """Serialize a Python value for a ``CAST(:param AS JSONB)`` bind parameter."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def build_finalize_params(result: ExecutionResult, finished_at: datetime) -> dict[str, Any]:
    """Build the bind parameters used to persist a terminal execution round."""
    if result.status not in TERMINAL_STATUSES:
        raise ValueError("Only terminal states may be persisted")

    model = result.model_result
    model_metadata = (
        {"provider": model.provider, "model": model.model, "fallback_used": model.fallback_used}
        if model is not None
        else None
    )

    return {
        "execution_id": str(result.execution_id),
        "status": result.status.value,
        "document_hashes": to_jsonb(dict(result.document_hashes) or None),
        "model_metadata": to_jsonb(model_metadata),
        "validated_output": to_jsonb(model.payload if model is not None else None),
        "operational_output": to_jsonb(result.operational_output),
        "review_reasons": to_jsonb(list(result.review_reasons)),
        "error_code": result.error_code,
        "duration_ms": result.duration_ms,
        "finished_at": finished_at,
    }


class JobRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def claim_pending(self, limit: int) -> list[tuple[str, JobInput, int]]:
        """Reserve jobs and create RUNNING rounds in one short transaction."""
        if limit <= 0:
            return []

        claim_sql = text("""
            SELECT q.*
              FROM analysis_job AS q
             WHERE q.processed = FALSE
               AND NOT EXISTS (
                    SELECT 1 FROM analysis_execution AS e
                     WHERE e.job_id = q.job_id AND e.status = 'RUNNING'
               )
             ORDER BY q.created_at, q.job_id
             LIMIT :limit
             FOR UPDATE OF q SKIP LOCKED
        """)

        claimed: list[tuple[str, JobInput, int]] = []
        with self.engine.begin() as connection:
            for row in connection.execute(claim_sql, {"limit": limit}).mappings().all():
                previous_round = connection.execute(text("""
                    SELECT COALESCE(MAX(round), 0)
                      FROM analysis_execution
                     WHERE job_id = :job_id
                """), {"job_id": row["job_id"]}).scalar_one()

                execution_id = str(uuid4())
                round_number = int(previous_round) + 1
                connection.execute(text("""
                    INSERT INTO analysis_execution
                        (execution_id, job_id, round, status, input_snapshot, started_at)
                    VALUES
                        (:execution_id, :job_id, :round, 'RUNNING',
                         CAST(:input_snapshot AS JSONB), :started_at)
                """), {
                    "execution_id": execution_id,
                    "job_id": row["job_id"],
                    "round": round_number,
                    "input_snapshot": to_jsonb(row["input_context"]),
                    "started_at": utc_now(),
                })
                claimed.append((execution_id, JobInput(
                    job_id=row["job_id"],
                    document_refs=tuple(row["document_refs"]),
                    context=dict(row["input_context"]),
                ), round_number))
        return claimed

    def finalize(self, result: ExecutionResult) -> None:
        params = build_finalize_params(result, finished_at=utc_now())

        with self.engine.begin() as connection:
            row = connection.execute(text("""
                UPDATE analysis_execution
                   SET status = :status,
                       document_hashes = CAST(:document_hashes AS JSONB),
                       model_metadata = CAST(:model_metadata AS JSONB),
                       validated_output = CAST(:validated_output AS JSONB),
                       operational_output = CAST(:operational_output AS JSONB),
                       review_reasons = CAST(:review_reasons AS JSONB),
                       error_code = :error_code,
                       duration_ms = :duration_ms,
                       finished_at = :finished_at
                 WHERE execution_id = :execution_id
                   AND status = 'RUNNING'
             RETURNING job_id
            """), params).mappings().first()

            if row is None:
                raise RuntimeError("Execution was not RUNNING; refusing to overwrite history")

            connection.execute(text("""
                UPDATE analysis_job
                   SET processed = TRUE, updated_at = :updated_at
                 WHERE job_id = :job_id
            """), {"job_id": row["job_id"], "updated_at": params["finished_at"]})
