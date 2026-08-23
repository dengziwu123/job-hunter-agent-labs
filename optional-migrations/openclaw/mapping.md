# Lab 6 to OpenClaw mapping

这份 mapping 用来指导迁移，不绑定某个 OpenClaw 版本的内部 API。课程已确认 OpenClaw 的公共入口是 CLI / Gateway；迁移时仍要读取当前官方文档，再通过 `adapter-contract.md` 把实际 runtime output映射到 Lab 7。

迁移源是一个 central coordinator 管理的 fixed sequential、model-backed multi-agent workflow。固定顺序是 `research -> summarize -> action -> approval -> stop`；它不是模型动态routing、parallel/loop/DAG或A2A。迁移可以替换runtime plumbing，但不能在没有新contract和acceptance tests的情况下悄悄改变拓扑或扩大agent权限。

| Lab 6 concept | Thin harness location | OpenClaw migration target |
| --- | --- | --- |
| Research role | `labs/lab_06/agents/research_agent.py` | Research agent role / instruction |
| Summarize role | `labs/lab_06/agents/summarize_agent.py` | Summarize agent role / instruction |
| Action role | `labs/lab_06/agents/action_agent.py` | Action agent role / instruction |
| Static orchestration plan | `labs/lab_06/agents/coordinator.py` | Explicit fixed workflow / step sequence, not a dynamic planner |
| Search-query planning | `research_agent.plan_query()` | One Research model task; not runtime routing or re-planning |
| Input/output contract | `labs/lab_06/contracts.py` + coordinator execution boundaries | Runtime required-field validation with failed trace events, not declaration-only validation; downstream schemas retain type/value checks |
| Tool boundary | Research uses the local source tool; coordinator owns the job-board MCP boundary; Action stays draft-only | OpenClaw tool permissions / allowed tool list |
| Approval policy | `labs/lab_06/guardrails.py` | OpenClaw guardrail, approval callback, policy node, or pre-action check |
| Budget | `labs/lab_06/config.py` | Charged `delegation` / `handoff` / `action_draft` event cap (`max_turns`), Research local `search_sources` cap (`max_tool_calls`), three role-model-boundary cap (`max_model_calls`), and explicit stop condition |
| Trace | `artifacts/lab_06/multi_agent_trace.jsonl` | OpenClaw trace, event log, run audit, or exported JSONL |
| Product workspace | `artifacts/lab_07/demo_run/.../application_workspace.json` | OpenClaw demo output folder or product workspace artifact |

Lab 6 的 `max_tool_calls` 只覆盖 Research 的 local `search_sources` call，`max_model_calls` 只覆盖三个 role-owned model boundaries。完整 Web stage 先运行的 Lab 2 structured model call，以及 Lab 4 job-board model/MCP protocol events和`tools/call`，仍要保留在完整 trace 和成本观察中，但不属于这两个局部 caps。

## Agent responsibilities

### ResearchAgent

Input:

- `query`
- planned `search_query`
- `source_items`

The coordinator also supplies profile/JD identifiers and the source limit as orchestration context.

Output:

- `search_query`
- complete `sources` records
- `source_ids`
- `source_snippets`

Rules:

- plans one query through its own model boundary
- can read local sources through the existing source tool
- does not create evidence notes; those belong to SummarizeAgent
- the coordinator sends the same planned query through the Lab 4 job-board MCP boundary and merges those records before context selection
- cannot write final application copy
- cannot send or apply

### SummarizeAgent

Input:

- selected `sources`
- `prior_report`
- `candidate_constraints`
- bounded `user_request`
- `skill_prompt`

Output:

- `FitGapReport`
- `evidence_notes`
- `prep_plan`

Rules:

- receives only sources retained by the Lab 4 context budget
- factual claims need evidence
- unsupported claims stay unsupported
- no invented experience

### ActionAgent

Input:

- `fit_gap_summary`
- `prep_plan`
- `requested_action`
- bounded `user_request`
- `skill_prompt`

Output:

- `action_type`
- policy `status`
- draft `content`
- policy `reason`

Rules:

- renders outreach, resume-bullet, or prep-plan content according to `requested_action`
- does not receive raw sources or `evidence_notes`; if a migration needs that evidence, extend the explicit contract and acceptance tests instead of reading hidden global state
- draft-only
- direct send/apply/update/publish requires approval
- unsupported claim in action is blocked

## What OpenClaw should replace

OpenClaw or another stable harness can replace:

- central coordinator execution for the same fixed step sequence
- session or run state
- subagent handoff and actual input/output payload validation
- tool dispatch plumbing
- runtime budget enforcement and explicit stop conditions
- trace viewer or run log storage

It should not replace:

- product safety policy
- evidence requirement
- draft-only boundary
- input/output contract
- student-owned Job Hunter product decisions

## Migration output contract

The migrated version should still produce a workspace with these fields or clear equivalents:

```json
{
  "demo_run_id": "...",
  "selected_profile": "...",
  "selected_jd": "...",
  "fit_gap_report": "...",
  "prep_plan": "...",
  "draft_actions": "...",
  "evidence_links": ["..."],
  "trace_links": ["..."],
  "eval_summary_link": "...",
  "audit_log_links": ["..."],
  "guardrail_status": "handled"
}
```
