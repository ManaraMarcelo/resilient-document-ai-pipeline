CREATE TABLE analysis_job (
    job_id BIGSERIAL PRIMARY KEY,
    document_refs JSONB NOT NULL,
    input_context JSONB NOT NULL,
    processed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analysis_execution (
    execution_id UUID PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES analysis_job(job_id),
    round INTEGER NOT NULL CHECK (round > 0),
    status TEXT NOT NULL CHECK (status IN (
        'RUNNING', 'COMPLETED', 'REVIEW_REQUIRED',
        'OUT_OF_SCOPE', 'PENDING_CONTEXT', 'FAILED'
    )),
    input_snapshot JSONB NOT NULL,
    document_hashes JSONB,
    model_metadata JSONB,
    validated_output JSONB,
    operational_output JSONB,
    review_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_code TEXT,
    duration_ms BIGINT CHECK (duration_ms >= 0),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (job_id, round),
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL)
        OR (status <> 'RUNNING' AND finished_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX one_running_execution_per_job
    ON analysis_execution (job_id)
    WHERE status = 'RUNNING';
