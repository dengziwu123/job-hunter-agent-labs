# OpenClaw migration prompt

把下面这段交给 Codex / Claude Code。它是课后迁移 prompt，不是课堂考核 prompt。

```text
You are helping me migrate my Lab 6 thin multi-agent Job Hunter Agent into OpenClaw.

Goal:
- Create a new OpenClaw-based version of my personal Job Hunter Agent.
- Preserve the behavior, contracts, guardrails, budget limits, and artifacts from Lab 6 and Lab 7.
- Do not rewrite Lab 1-5.

First read the current repo context:
- job-hunting-product-spec.html (attach or copy this file from the course release directory first)
- instructions/lab-06-thin-multi-agent-harness.md
- instructions/lab-07-product-demo.md (if Lab 7 is already installed)
- optional-migrations/openclaw/mapping.md
- optional-migrations/openclaw/adapter-contract.md
- optional-migrations/openclaw/acceptance-checklist.md
- labs/lab_06/agents/research_agent.py
- labs/lab_06/agents/summarize_agent.py
- labs/lab_06/agents/action_agent.py
- labs/lab_06/contracts.py
- labs/lab_06/guardrails.py
- labs/lab_06/config.py
- labs/lab_07/app/main.py

Then inspect the current OpenClaw installation, examples, or official docs available in this environment. Do not guess OpenClaw APIs. If OpenClaw is not installed or the API cannot be verified, create a migration plan and scaffold files with TODOs instead of fake runnable code.

Do not modify:
- labs/lab_01
- labs/lab_02
- labs/lab_03
- labs/lab_04
- labs/lab_05
- labs/lab_06/tests
- labs/lab_07/tests
- labs/shared
- fixtures or golden outputs

Create a new directory:
- openclaw_job_hunter/

The new directory should include:
- README.md with setup and run commands
- agent role definitions for ResearchAgent, SummarizeAgent, and ActionAgent
- a central coordinator with the fixed `research -> summarize -> action -> approval -> stop` sequence
- tool/action policy definitions
- required-field validation on each role's actual input/output payload
- approval guardrail behavior
- budget or stop-condition config
- trace or audit output
- a smoke-test or validation script
- MIGRATION_REPORT.md
- adapter.py implementing run_job_agent(materials=..., messages=...) for the Lab 7 UI

Preserve these behaviors:
- ResearchAgent plans one query and returns complete source records, source ids, and source snippets; it does not create evidence notes.
- The coordinator owns the job-board MCP boundary, merges its records with local ResearchAgent sources, and applies context selection before the next handoff.
- SummarizeAgent receives selected sources, prior report, candidate constraints, bounded user request, and Skill prompt; it returns a fit/gap report, evidence notes, and prep plan.
- ActionAgent receives fit summary, prep plan, requested action, bounded user request, and Skill prompt; it does not receive raw sources or evidence notes and only creates draft actions.
- The workflow remains central-coordinator and fixed-sequential; do not silently turn it into model-selected routing, a planning loop, parallel/DAG execution, or A2A.
- Direct send/apply/update/publish requests require approval.
- Fake experience is blocked.
- Unsupported claims are marked unsupported.
- Every factual claim either has evidence or is explicitly unsupported.
- The run writes trace or audit artifacts that can be inspected later.

Acceptance checks:
- Same synthetic profile + local JD input works.
- Output includes fit/gap report, prep plan, and at least one draft action.
- Direct send/apply request does not execute.
- Fake experience prompt is blocked.
- Unsupported claim is not treated as supported.
- Budget or stop condition is enforced.
- The migration report explains how Lab 6 concepts map into OpenClaw.
- The adapter returns actual agent_run and per-task eval_summary data matching adapter-contract.md.

Before editing:
- Summarize the files you read.
- Explain which OpenClaw API or example you are using.
- List the files you plan to create under openclaw_job_hunter/.

After editing:
- Run the smoke test if possible.
- If OpenClaw cannot run locally, explain what blocked it and leave a clear manual run command.
- Summarize remaining TODOs in MIGRATION_REPORT.md.
```

## 迁移时要坚持的边界

不要让 OpenClaw 版本新增真实外部发送、投递、发布或修改外部系统。Job Hunter Agent 的外部动作仍然是 draft-only，除非显式 human approval。

不要把 Lab 1-5 的教学 scaffold 改成 OpenClaw。Lab 1-5 是为了学习 primitives，OpenClaw migration 是课后产品化分支。
