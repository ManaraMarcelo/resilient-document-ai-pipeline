import json
from dataclasses import replace
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from models import ExecutionResult, ExecutionStatus, ModelResult
from repository import JobRepository, build_finalize_params

FINISHED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def completed_result() -> ExecutionResult:
    return ExecutionResult(
        execution_id=uuid4(),
        status=ExecutionStatus.COMPLETED,
        attempts=1,
        duration_ms=42,
        model_result=ModelResult(provider="primary", model="model-x", fallback_used=False, payload={"label": "A"}),
        document_hashes={"doc-1": "sha256:abc"},
        operational_output={"decision": "A"},
        review_reasons=(),
    )


class BuildFinalizeParamsTests(unittest.TestCase):
    def test_json_columns_are_serialized(self) -> None:
        params = build_finalize_params(completed_result(), FINISHED_AT)
        self.assertEqual(json.loads(params["validated_output"]), {"label": "A"})
        self.assertEqual(json.loads(params["operational_output"]), {"decision": "A"})
        self.assertEqual(json.loads(params["review_reasons"]), [])
        self.assertEqual(json.loads(params["document_hashes"]), {"doc-1": "sha256:abc"})
        self.assertEqual(
            json.loads(params["model_metadata"]),
            {"provider": "primary", "model": "model-x", "fallback_used": False},
        )
        self.assertEqual(params["status"], "COMPLETED")
        self.assertEqual(params["finished_at"], FINISHED_AT)

    def test_result_without_model_has_null_model_columns(self) -> None:
        result = ExecutionResult(
            execution_id=uuid4(),
            status=ExecutionStatus.REVIEW_REQUIRED,
            attempts=0,
            duration_ms=5,
            review_reasons=("AMBIGUOUS_TARGET",),
        )
        params = build_finalize_params(result, FINISHED_AT)
        self.assertIsNone(params["model_metadata"])
        self.assertIsNone(params["validated_output"])
        self.assertIsNone(params["document_hashes"])
        self.assertEqual(json.loads(params["review_reasons"]), ["AMBIGUOUS_TARGET"])

    def test_non_positive_limit_does_not_touch_database(self) -> None:
        self.assertEqual(JobRepository(engine=None).claim_pending(0), [])  # type: ignore[arg-type]


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class JobRepositoryIntegrationTests(unittest.TestCase):
    """Runs against a disposable PostgreSQL database. All tables are dropped and recreated."""

    def setUp(self) -> None:
        from sqlalchemy import create_engine, text

        self.text = text
        self.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS analysis_execution, analysis_job"))
            connection.exec_driver_sql(SCHEMA_SQL.read_text())
            connection.execute(text("""
                INSERT INTO analysis_job (document_refs, input_context)
                VALUES (CAST(:refs AS JSONB), CAST(:ctx AS JSONB))
            """), {"refs": json.dumps(["ref-1"]), "ctx": json.dumps({"kind": "synthetic"})})
        self.repository = JobRepository(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_claim_then_finalize_appends_history(self) -> None:
        claimed = self.repository.claim_pending(limit=10)
        self.assertEqual(len(claimed), 1)
        execution_id, job, round_number = claimed[0]
        self.assertEqual(job.document_refs, ("ref-1",))
        self.assertEqual(round_number, 1)

        # An active round prevents a second claim.
        self.assertEqual(self.repository.claim_pending(limit=10), [])

        result = completed_result()
        result = replace(result, execution_id=execution_id)
        self.repository.finalize(result)

        with self.engine.connect() as connection:
            row = connection.execute(self.text(
                "SELECT status, model_metadata, document_hashes FROM analysis_execution"
            )).mappings().one()
            processed = connection.execute(self.text("SELECT processed FROM analysis_job")).scalar_one()
        self.assertEqual(row["status"], "COMPLETED")
        self.assertEqual(row["model_metadata"]["provider"], "primary")
        self.assertEqual(row["document_hashes"], {"doc-1": "sha256:abc"})
        self.assertTrue(processed)

        # Terminal rounds are immutable.
        with self.assertRaises(RuntimeError):
            self.repository.finalize(result)


if __name__ == "__main__":
    unittest.main()
