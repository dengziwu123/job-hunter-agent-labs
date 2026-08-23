# OpenClaw UI adapter contract

Lab 7 does not import an `openclaw` Python package. OpenClaw is a local CLI / Gateway runtime. A completed migration places this file in the student workspace:

```text
openclaw_job_hunter/adapter.py
```

The local UI enables its OpenClaw backend only when both are true:

1. `openclaw` is available on `PATH`.
2. `openclaw_job_hunter/adapter.py` exists.
3. The migration smoke test produced `openclaw_job_hunter/acceptance.json` for contract version 1.

The adapter exports:

```python
def run_job_agent(*, materials: list[dict], messages: list[dict]) -> dict:
    ...
```

The Lab 7 CLI may also call it with no arguments, so defaults for the synthetic profile, JD, and prompt are required.

A normal implementation can invoke the verified local CLI surface, for example:

```text
openclaw agent --agent job-hunter --message-file <prompt-file> --json
```

Use the current official OpenClaw CLI docs before writing that process adapter; do not assume its JSON response shape. Parse the actual response, preserve runtime IDs/events, then return this course contract:

```json
{
  "agent_run": {
    "run": {"run_id": "...", "stage": "openclaw"},
    "profile": {"id": "..."},
    "job_description": {"id": "..."},
    "summary": {
      "fit_gap_report": {
        "fit_summary": "...",
        "strengths": [],
        "gaps": [],
        "risks": [],
        "missing_info": [],
        "recommended_next_steps": []
      },
      "evidence_notes": [{"claim": "...", "status": "supported", "source_id": "..."}],
      "prep_plan": {}
    },
    "action": {"action_type": "outreach_draft", "status": "draft_created", "content": "..."},
    "approval": {"status": "allowed_draft", "reason": "..."},
    "trace_events": [
      {"event": "tool_call", "component": "...", "operation": "..."},
      {"event": "summary_output", "component": "...", "operation": "..."},
      {"event": "approval_decision", "component": "...", "operation": "..."},
      {"event": "stop", "component": "...", "operation": "..."}
    ]
  },
  "eval_summary": {
    "run": {"run_id": "...", "stage": "openclaw_eval"},
    "total": 1,
    "passed": 1,
    "failed": 0,
    "results": [{"task_id": "...", "passed": true, "reason": "..."}]
  }
}
```

The protected Lab 7 runtime validates this structure, required trace events, non-empty per-task eval evidence, same-run artifact links, report shape, evidence, action, and approval status. A model-generated statement such as `"all tests passed"` is not sufficient; the adapter must run and return its actual acceptance cases.

After the smoke test actually passes, it may write:

```json
{
  "status": "passed",
  "contract_version": 1,
  "checks": [
    "supported_evidence",
    "unsupported_claim",
    "fake_experience",
    "direct_send_approval",
    "trace_events",
    "budget_or_stop"
  ]
}
```

Do not commit a pre-filled acceptance file. The UI treats a missing, malformed, incomplete, or failed file as unavailable.

Official references checked for this course design:

- [OpenClaw `agent` CLI](https://docs.openclaw.ai/cli/agent)
- [OpenClaw Gateway OpenResponses API](https://docs.openclaw.ai/gateway/openresponses-http-api)
- [OpenClaw skills](https://docs.openclaw.ai/skills)
