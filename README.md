<p align="center">
  <img src="assets/banner.jpg" alt="Resilient Document AI Pipeline" width="100%">
</p>

# Resilient Document AI Pipeline

> Durable Python workers for document analysis with transactional claims, structured LLM output, deterministic validation, and auditable recovery.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-required-336791)

## Executive Summary

This project demonstrates a production-oriented architecture for processing document-analysis jobs with large language models without treating an LLM response as the source of truth.

It combines durable queue orchestration, document preparation, strict structured generation, deterministic validation, append-only history, and human-review outcomes. The design is useful whenever document inputs are variable in quality and an incorrect automatic result is more costly than an explicit review request.

## Problem Statement

Document AI workers face several interacting reliability problems:

- Multiple workers may claim the same item unless claiming is transactional.
- A document may have valid text, corrupted text, no embedded text, or several candidate sections.
- A model can return malformed JSON, unsupported values, weak evidence, or an answer that is structurally valid but operationally unsafe.
- Provider outages and incomplete responses must not silently become a domain decision.
- Reprocessing must preserve prior outcomes and remain auditable.
- Logs must be useful for diagnosis without recording document content, prompts, credentials, or images.

The architecture handles those concerns as separate layers rather than embedding them in a single model call.

## System Architecture

```mermaid
flowchart TD
    A[Input source] --> B[Manual queue or synchronous request]
    B --> C[Transactional job claim]
    C --> D[Append-only execution round]
    D --> E[In-memory document retrieval]
    E --> F[Native PDF text extraction]
    F --> G{Text usable?}
    G -- Yes --> H[Structural candidate ranking]
    G -- No or degraded --> I[Conditional visual reading graph]
    I --> H
    H --> J{Selection unambiguous?}
    J -- No --> K[Review-required result]
    J -- Yes --> L[Structured LLM classification]
    L --> M[JSON Schema validation]
    M --> N[Semantic and evidence validation]
    N --> O[Deterministic result composer]
    O --> P[Atomic terminal persistence]
    K --> P
    P --> Q[Current-result projection]
    P --> R[Correlated telemetry]
    L -. technical failure .-> S[Secondary provider]
    S --> M
```

## Processing Lifecycle

1. An upstream system writes a complete input row, including opaque document references and the structured context required by the worker.
2. Workers reserve eligible rows in a short database transaction. The reservation creates a new immutable execution round in `RUNNING` state.
3. The worker retrieves documents into memory, computes integrity hashes, and extracts native PDF text page by page.
4. The document selector builds candidates from structure and evidence. Optional embeddings improve ranking but do not replace textual proof.
5. If native text is absent or demonstrably degraded, a small LangGraph flow invokes visual reading only for that condition. Visual results are checked for page coverage and ordering.
6. The selected context is submitted to an LLM gateway with a strict JSON schema.
7. The response undergoes defensive JSON parsing, schema validation, evidence-location checks, and deterministic policy validation.
8. The worker either composes a safe operational result or returns an explicit review/out-of-scope/failed outcome.
9. A final transaction writes the terminal round, its audit metadata, and the queue completion marker. Prior rounds are never overwritten.

## Key Architectural Decisions

### Transactional Job Claiming

The repository reserves work with a database transaction and `FOR UPDATE SKIP LOCKED`. This lets multiple worker processes compete safely: a row already held by one worker is skipped instead of blocking the entire batch.

The transaction creates the execution row at claim time, so an active round itself is also a concurrency guard. A partial unique index prevents two active rounds for the same queue item.

### Append-Only History and Reprocessing

Each execution has a monotonically increasing round number and an optional link to its predecessor. Reprocessing creates another row; it does not edit a prior result.

A projection query selects the latest terminal round. This deliberately avoids hiding a previously usable result while a newer retry is still running.

### Defensive Document Preparation

The pipeline treats a PDF as an unreliable transport format. It records a content hash, page count, page-level text quality, and selection audit information. It does not assume that a final page, a filename, or an isolated heading is enough evidence.

The visual path is conditional. It is a recovery mechanism for unusable text, not the default path for every document.

### Structured LLM Generation

The LLM gateway accepts an optional output contract containing a schema name and a JSON Schema. When supported, the provider receives native strict structured-output instructions. The application still performs local validation because API-level conformance does not prove semantic correctness.

### Technical Fallback, Not Decision Fallback

The gateway can route a request to a secondary provider after a technical failure. Configuration errors and schema-contract errors are returned directly; they do not trigger an unrelated provider switch. This preserves diagnostic clarity and prevents retrying a deterministic configuration defect.

### Deterministic Validation and Composition

Probabilistic extraction is followed by deterministic checks for required evidence, relationship consistency, scope constraints, and output completeness. A separate composer turns validated facts into the downstream record. This keeps policy code inspectable and testable without asking the model to reproduce deterministic formatting.

### Bounded Ambiguity

Candidate routing is limited to a small ranked set. If the worker cannot establish a unique, sufficiently supported target, it emits a review-required result instead of forcing a classification.

## State Machine

```mermaid
stateDiagram-v2
    [*] --> READY
    READY --> RUNNING: transactional claim
    RUNNING --> COMPLETED: validated result
    RUNNING --> REVIEW_REQUIRED: ambiguity or insufficient evidence
    RUNNING --> OUT_OF_SCOPE: supported but excluded input
    RUNNING --> PENDING_CONTEXT: required upstream context missing
    RUNNING --> FAILED: technical or contract failure
    COMPLETED --> READY: explicit reprocess request
    REVIEW_REQUIRED --> READY: explicit reprocess request
    OUT_OF_SCOPE --> READY: explicit reprocess request
    FAILED --> READY: explicit reprocess request
```

`READY` represents a queue item eligible for a new round. Terminal outcomes are immutable; an explicit upstream action is required before another round begins.

## Data Model

| Entity | Field | Type | Purpose |
|---|---|---|---|
| Input queue | `job_id` | bigint/uuid | Stable work-item identifier |
| Input queue | `document_refs` | JSON/text | Opaque references to source documents |
| Input queue | `processed` | boolean | Eligibility flag controlled by the upstream integration |
| Execution history | `execution_id` | UUID | Correlation id for one processing round |
| Execution history | `round` | integer | Monotonic reprocessing version |
| Execution history | `status` | enum | Lifecycle outcome |
| Execution history | `input_snapshot` | JSONB | Sanitized structured context used by the round |
| Execution history | `document_hashes` | JSONB | Integrity markers without retaining file bytes |
| Execution history | `model_metadata` | JSONB | Provider/model/fallback metadata |
| Execution history | `validated_output` | JSONB | Schema-validated model output |
| Execution history | `operational_output` | JSONB | Deterministic downstream result |
| Execution history | `review_reasons` | JSONB | Machine-readable escalation reasons |
| Execution history | `duration_ms` | bigint | End-to-end processing duration |
| Execution history | `started_at`, `finished_at` | timestamptz | Lifecycle timestamps |

## Sanitized Schema Example

```sql
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
```

## Reliability Strategy

- **Atomic claim/finalize boundaries:** A round is inserted at reservation time and finalized only from `RUNNING` state.
- **Bounded retries:** Calls that can be transiently unavailable receive bounded retry budgets. Invalid structured output has a separate bounded retry path.
- **Explicit terminal classification:** Model/schema errors, document ambiguity, missing context, and excluded inputs remain distinguishable.
- **No silent replacement:** A new round never overwrites historical output.
- **Document reference consistency:** Finalization can verify that source references did not change during processing; a changed input remains eligible for a new round.
- **In-memory sensitive processing:** Retrieved bytes and temporary visual inputs are not stored in the execution history.

## AI Reliability Strategy

- Use a generic extraction instruction that requests only schema-conforming JSON and evidence references.
- Prefer provider-native strict structured outputs when available.
- Parse defensively and validate again with local JSON Schema checks.
- Require evidence to be traceable to the selected document context.
- Use deterministic semantic checks to reject contradictory, incomplete, or unsupported outputs.
- Route technical failures to a compatible secondary provider while preserving the same output contract.
- Keep routing and visual transcription contracts smaller than the full classification contract.
- Escalate uncertainty to `REVIEW_REQUIRED` rather than inventing a result.

### Generic Prompt Pattern

```text
You are a document extraction component.

Use only the supplied document context. Do not infer missing facts.
Return exactly one JSON object that conforms to the supplied schema.
For every extracted conclusion, include a short evidence fragment and source page.
If the context is insufficient or ambiguous, set the appropriate review flag and explain why using only allowed enum values.
```

This is a reconstructed public-safe pattern, not an export of an operational prompt.

## Observability

The architecture emits structured lifecycle events with:

- execution correlation id;
- queue/job id;
- current stage;
- terminal status;
- attempt count;
- duration;
- selected provider and whether fallback was used;
- sanitized error code.

Log design intentionally excludes complete document text, images, prompts, secrets, and authentication material. Execution records persist hashes, versions, validation summaries, and bounded error messages for auditability.

## FinOps / LLM Cost Controls

- Model/provider selection is configuration-driven rather than hard-coded in orchestration logic.
- The normal path uses native text extraction; visual multimodal processing is conditional.
- Candidate routing is skipped when deterministic evidence is sufficient.
- Routing, classification, and visual recovery have separate bounded retry budgets.
- Context is narrowed to selected pages/blocks before model invocation.
- Optional embeddings assist ranking but do not require a model call when unavailable.

No public cost or throughput figures are disclosed.

## Trade-offs

| Decision | Benefit | Cost |
|---|---|---|
| Strict output schemas | Predictable integration boundary | Less flexibility for exploratory answers |
| Deterministic post-validation | Safer automation | More implementation and test complexity |
| Append-only rounds | Strong audit trail and safe reprocessing | More storage and projection logic |
| Conditional visual fallback | Better recovery for degraded PDFs | Higher latency and model cost for affected documents |
| Transactional row locks | Prevents duplicate work | Requires a relational database and careful transaction scope |
| Conservative escalation | Avoids forced answers | More items may require human review |

## Production Lessons

- A valid JSON response is not equivalent to a safe business result.
- Evidence provenance should be part of the output contract, not an afterthought.
- Provider fallback should target transport and availability failures, not conceal deterministic contract defects.
- Keeping the latest terminal result visible during a retry improves downstream availability.
- A visual fallback is most useful as a narrow recovery path after text-quality detection, not as a blanket replacement for native extraction.
- Reprocessing is an audit feature: model, schema, prompt, and input versions should be recorded per round.

## Repository Structure

```text
.
├── src/
│   ├── models.py          # Immutable execution contracts and lifecycle states
│   ├── llm_gateway.py     # Strict structured-output gateway with technical fallback
│   ├── document_flow.py   # Native-first extraction with conditional visual recovery
│   └── repository.py      # Transactional claim (SKIP LOCKED) and append-only finalization
├── sql/
│   └── schema.sql         # PostgreSQL tables, constraints, and partial unique index
├── assets/                # README banner
├── tests/                 # unittest suite (unit + optional PostgreSQL integration)
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Getting Started

Requirements: Python 3.11+ (uses `enum.StrEnum`) and PostgreSQL.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in local values; never commit .env
```

The modules in `src/` use flat imports (e.g. `from models import ...`), so run them with `src` on the path:

```bash
PYTHONPATH=src python -c "import models, llm_gateway, document_flow, repository"
```

Create the tables:

```bash
psql "postgresql://user:password@localhost:5432/document_ai" -f sql/schema.sql
```

## Running Tests

```bash
python -m unittest -v
```

Unit tests use in-memory fakes and need no external services. The repository integration test runs only when `TEST_DATABASE_URL` points to a **disposable** PostgreSQL database (its tables are dropped and recreated):

```bash
docker run -d --rm --name docai-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=docai_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+psycopg://postgres:test@localhost:55432/docai_test python -m unittest -v
docker stop docai-test-pg
```

## Disclaimer

The code in `src/` consists of reconstructed, generalized, synthetic examples intended to demonstrate engineering concepts. They are intentionally smaller than a production system, are not direct exports of any original implementation, and do not contain proprietary naming, prompts, business rules, or data.

---

## Author

**Marcelo Manara** — Software Engineer | AI Systems · Cloud · Python · AWS

[![LinkedIn](https://img.shields.io/badge/LinkedIn-marcelo--manara-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/marcelo-manara/)
[![GitHub](https://img.shields.io/badge/GitHub-ManaraMarcelo-181717?style=flat&logo=github&logoColor=white)](https://github.com/ManaraMarcelo)
[![Portfolio](https://img.shields.io/badge/Portfolio-manaramarcelo.dev-6E40C9?style=flat&logo=googlechrome&logoColor=white)](https://manaramarcelo.dev)
