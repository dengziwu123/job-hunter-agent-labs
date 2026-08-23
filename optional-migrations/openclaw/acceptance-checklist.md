# OpenClaw migration acceptance checklist

Use this checklist before showing the OpenClaw version in Lab 7 or keeping it as a personal tool.

## Setup

- `openclaw` CLI is present on `PATH`; the migration does not test for a Python package named `openclaw`.
- On Windows, the CLI is visible from the same PowerShell or WSL2 environment that starts the course UI.
- OpenClaw dependency or local runner is documented.
- `README.md` has a clear setup command.
- `README.md` has a clear run command.
- The migration does not require editing Lab 1-5.
- API keys are read from local env, not committed.
- `openclaw_job_hunter/adapter.py` implements the contract in `adapter-contract.md`.
- The smoke test writes `openclaw_job_hunter/acceptance.json` only after all six required acceptance categories pass.

## Product behavior

- Same synthetic profile + local JD input runs.
- Output includes a fit/gap report.
- Output includes a prep plan.
- Output includes at least one draft action.
- Workspace or output folder links evidence, trace, eval, and audit artifacts.

## Safety

- Direct send/apply/update/publish does not execute by default.
- Direct external action returns `needs_approval` or equivalent.
- Fake experience is blocked or rejected.
- Unsupported claim stays unsupported.
- Resume bullet suggestions only rewrite supported profile facts.

## Harness behavior

- ResearchAgent, SummarizeAgent, and ActionAgent are separate model-backed roles.
- A central coordinator preserves the fixed `research -> summarize -> action -> approval -> stop` sequence; the migration does not silently replace it with model routing, a planning loop, parallel/DAG execution, or A2A.
- Research hands off source records to Summarize; Summarize returns the fit/gap report, evidence notes, and prep plan; Action receives fit summary and prep plan but not raw sources/evidence notes.
- Required-field validation runs on each role's actual input and output payload before the final workspace is accepted.
- Budget or stop condition is enforced.
- Trace or audit log shows agent handoff and approval decision.
- Adapter output contains real per-task eval results; a generated `"passed"` claim is not accepted as an eval.

## Lab 7 demo

If using the OpenClaw version for Lab 7, the demo must show:

- the OpenClaw run command
- the selected profile and JD
- the generated workspace or equivalent output
- one supported claim with evidence
- one unsupported or fake-experience guardrail case
- one direct-send/apply request routed to approval
- a short explanation of what OpenClaw handles versus what your product policy handles

## Migration report

`MIGRATION_REPORT.md` should answer:

- Which OpenClaw API, example, or docs did you use?
- Which Lab 6 files did you map?
- What changed from the thin harness version?
- What stayed the same?
- What is still a TODO?
- Did the smoke test pass?
