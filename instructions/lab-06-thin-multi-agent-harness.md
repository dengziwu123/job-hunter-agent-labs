# Lab 6: Thin Multi-Agent Harness

Phase: **Phase 5: Multi-Agent Orchestration**

## 先给这个系统定性

本 Lab 实现的是一个 **central coordinator 管理的、fixed sequential、model-backed multi-agent workflow**：coordinator 预先写死 `research -> summarize -> action -> approval -> stop` 的顺序；Research、Summarize、Action 分别有独立的 role instruction、模型调用边界和 input/output contract。

这里的 agent 不等于“起了一个名字的函数”。本课把同时具备以下边界的执行角色称为 agent：

- 独立的 role instruction 和模型执行上下文
- 明确的输入、输出和失败 contract
- 受限制的能力或权限边界

因此三个模型角色是 agents；job-board MCP server、local source tool、evidence verifier 和 approval policy 都是被 coordinator 或 agent 调用的能力，不会因为参与 workflow 就自动变成 agent。

这个 Lab **不是** parallel agents、模型动态 routing、反复 planning/re-planning 的 loop、DAG executor，也不是去中心化 A2A。它故意选择最容易观察的中央协调、固定顺序，用来学习拆分决策和 handoff，而不是展示所有 multi-agent 拓扑。

## 为什么不是一开始就用 multi-agent

Lab 5 已经证明 single workflow 的行为可以被 eval，但 research、summary 和 action 仍然共享一条说不清的链路：谁拿到什么输入、失败后交给谁、预算花在哪里、什么时候停止，都不够明确。现在才引入 roles，是因为你已经认识每个要分出去的 primitive，不会把 multi-agent 误解成“多写几个 prompt”。

multi-agent 不是 single-agent 的默认升级。只有拆分带来的边界收益大于额外的模型调用、延迟、token 成本、协调复杂度和 handoff 信息损失时，才值得使用。本 Lab 的拆分理由是：

- **context boundary**：Summarize 只接收筛选后的 sources；Action 从上游产物中只接收 fit summary 和 prep plan，再加 shared request / Skill，不需要重新读取全部原始资料
- **tool / permission boundary**：Research 使用查询和来源能力；Action 只生成本地 draft，不拥有发送、申请或修改外部系统的权限
- **risk / ownership boundary**：evidence、draft policy 和 approval decision 分别留在可追踪的步骤中，失败责任和停止位置更清楚

如果任务很短、所有步骤需要同一份上下文和同一组工具，或 handoff 会丢掉完成任务所需的信息，就先保留 single-agent / single workflow。若看不出可验证的质量、安全或并行收益，却只增加了角色和调用次数，也不该为了“用了 multi-agent”而拆分。

进入 Lab 6 前，应先完成 Lab 4代码并让 Lab 5 eval全绿；这是课程completion gate。Lab 6 runtime本身从当前Job Materials启动完整Agent，不读取Lab 4/5旧artifact。multi-agent仍然不能绕过前面的代码primitive和质量标准。

三篇延伸阅读分别提供不同的观察角度：[Anthropic](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them) 用 context isolation、parallelism、specialization 与 coordination cost 判断何时值得拆；[Google Cloud ADK](https://cloud.google.com/blog/topics/developers-practitioners/building-collaborative-ai-a-developers-guide-to-multi-agent-systems-with-adk) 对比 sequential、parallel、loop 等协作拓扑；[LangChain](https://www.langchain.com/blog/planning-agents) 展示 planner/executor、re-planning 和 DAG 类 planning。它们在本 Lab 中是设计判断材料，不是 required SDK/API 实作。

## 目标

用课程代码模板里的 thin multi-agent harness 跑一条范围有限的 workflow。这节不考 OpenClaw、ADK 或某个 SDK 的 API 记忆。你要做的是把 role instruction、input/output contract、guardrail、budget 和 trace 组织成一个能维护的 agentic product。复杂 scheduler 和 production runtime 不在这节实现。

这节主要看：

- coordinator 使用可见、固定且有停止条件的 `research -> summarize -> action` execution plan；不让模型临时生成无限计划
- research 用自己的 role instruction 把 bounded request 规划成一条查询；coordinator 把同一查询交给 Lab 4 current-opening job-board MCP 判断，再调用既有 local source tool
- research 返回来源后，coordinator 先复用 Lab 4 `select_context()`，为 summarize 固定 instructions、Skill 规则和 bounded user request 预留 token，再把 keep/truncate 后的来源交给 summarize
- research / summarize / action 三个 agent 分工清楚
- 每个 agent 有 input/output contract
- coordinator 有 orchestration step/event、tool call 和 role-owned model call 上限
- risky action 进入 approval guardrail
- coordinator 会实际调用三个 agent 的最小 `run()`，trace 能复盘 delegation 和 decision
- 学生能用同一输入比较 Lab 5 与 Lab 6 的调用成本、context isolation 和 handoff 信息损失，而不是预设 multi-agent 一定更好

模型在这个 harness 里的位置很清楚：research、summarize 和 action 都是由各自 role instruction 驱动的 subagent。research 规划一条查询，课程 scaffold 随后复用 Lab 4 的 model-to-MCP job-board 判断，并调用 Lab 3 `search_sources()`；current-opening records 与 local records 合并后才进入 context selection。summarize 和 action 分别生成 summary 与指定类型的 draft。三个 role calls 都通过课程提供的 `labs.lab_06.model.traced_complete(..., instruction=INSTRUCTION)`，并各占一个 `max_model_calls` 预算槽位；Lab 4 job-board 判断保留自己的 model/MCP trace。没有 API key 时，这些边界使用确定性 offline fallback，所以单测和主线仍可 offline 跑通；如果你想判断 instruction 对真实模型的影响，再补一次 `mode=live` 对照。

## 本 lab 的产出

完成后会生成：

```text
artifacts/lab_06/multi_agent_trace.jsonl
```

trace 里要能看到任务怎样被分配、agent 怎样交接、调用了哪些 tool、生成了什么 summary，以及 approval decision。

Web run还会生成：

```text
artifacts/lab_06/runs/<run_id>/multi_agent_result.json
artifacts/lab_06/runs/<run_id>/multi_agent_trace.jsonl
```

## 修改范围

只改这些文件：

```text
labs/lab_06/agents/research_agent.py
labs/lab_06/agents/summarize_agent.py
labs/lab_06/agents/action_agent.py
labs/lab_06/guardrails.py
labs/lab_06/config.py
```

这些文件不改：

```text
labs/lab_06/agents/coordinator.py
labs/lab_06/tests/
labs/shared/
```

可以阅读 coordinator，理解 thin harness scaffold 怎样串起来；默认不需要重写它。

本 Lab 中容易混淆的三种 planning 必须分开：

| 名称 | 本 Lab 是否实现 | 含义 |
| --- | --- | --- |
| static orchestration plan | 是 | Python coordinator 固定执行 `research -> summarize -> action -> approval -> stop` |
| search-query planning | 是 | Research 模型把 bounded user request 改写成一条查询；它不决定 workflow 的下一步 |
| dynamic planner / re-planning | 否 | 模型生成或修改多步执行计划，并根据中间结果重新规划 |

学生要能读懂、验证和解释前两种 plan，但本 Lab 不要求实现 dynamic planner、production scheduler、parallel graph 或模型 routing。

## 已经搭好的部分

代码模板已经准备好：

- thin multi-agent coordinator scaffold
- research query-planning prompt scaffold，以及 Lab 4 current-opening job-board MCP 的复用 wiring
- 两个 model input 的 context budget wiring：`research -> select_context -> summarize` 选择证据，action 调用前再按完整 prompt 独立计量；两边都包含实际发送的 role system instruction（学生不重写）
- agent registry + fixed dispatch wiring
- Gemini model helper（`labs/lab_06/model.py`，无 key 时自动降级 offline）
- trace writer
- fake approval service
- unsafe action prompts
- Lab 6 tests

你要补的是 agent instructions、contracts、最小 `run()` 输出、budget config 和 approval branches。

这些 agent 不是重新实现前面的业务能力：

- research agent 的 instruction 必须进入 query-planning system prompt，随后调用 Lab 3 的 `search_sources`
- summarize agent 必须返回 Lab 2 的 `FitGapReport`，并调用 Lab 4 evidence verifier
- summarize agent 收到的是 Lab 4 context budget 已选中的 sources；prompt 必须用课程提供的 `render_summarize_prompt()`，保持固定 instructions、Skill、bounded user request 和 evidence 的预算口径与真实输入一致
- action agent 必须调用 Lab 3 的 `draft_action`
- action prompt 和 offline fallback 必须按 `outreach_draft`、`resume_bullet`、`prep_plan` 分支；coordinator 会把 role system instruction 与同一份 user prompt 一起计量，超出 context budget 时不会发起 action model call
- summarize / action agent 的 input contract 必须接收 coordinator 传入的 `skill_prompt` 和 `user_request`，并把两者放进各自真实发送的 model prompt；三个 agent 也必须把自己的 instruction 传到 system prompt。Lab 4 的学生规则、Lab 3 的 bounded request 和 Lab 6 role 定义都不能退化成 metadata
- Lab 6 model helper 在 live mode 必须调用 Lab 1 的 `ModelClient`
- Lab 6 guardrail 只做 orchestration status mapping，不复制一份 policy 关键词表

## 任务

### 1. 先跑 unit tests

```bash
uv run pytest labs/lab_06/tests
```

先看失败是否来自 instruction placeholder、contract mismatch、budget config 或 approval branch。

新增 behavior tests还会注入变化的 model query、`source_items`和 fake policy，验证 agents真的调用前序 primitives。只填 instruction但保留 hardcoded output不能通过。

### 2. 补三个 agent instructions

打开：

```text
labs/lab_06/agents/research_agent.py
labs/lab_06/agents/summarize_agent.py
labs/lab_06/agents/action_agent.py
```

三个 agent 的边界建议如下：

- research agent：调用 Lab 3 tool，输出完整 sources、ids和 snippets
- summarize agent：用 Lab 2 report contract和 Lab 4 evidence notes输出 report/prep plan
- action agent：生成 draft后交给 Lab 3 policy，不执行外部动作

每个 `INSTRUCTION` 都有 `role`、`objective`和`boundary`三个字段。保持简短，但要写清这个 agent 是谁、要完成什么、不能越过什么边界；普通字符串或只删除 TODO 不能通过。

每个 agent 文件里还有一个 `run(input) -> output`，要返回符合 contract 的结构。research 的 `plan_query()` scaffold会用你的 instruction规划查询，`run()`再用该查询组装 source snippets；summarize 和 action 用 `labs.lab_06.model.traced_complete(..., instruction=INSTRUCTION)` 生成 summary / draft 文本，并且必须把 input 里的 `skill_prompt` 和 bounded `user_request` 放进真实 prompt。`offline_text` 是无 key 时的确定性回退。coordinator 会调用这些函数，再把成功或失败的模型边界写进 trace。

### 3. 补 input/output contracts

每个 agent 都要有稳定 contract。最少要能回答：

- 接收什么字段
- 返回什么字段
- 失败时返回什么 status
- trace 里记录什么 event

coordinator 是课程提供且固定的，所以学生填写的 required fields 不是开放式扩展点，必须与实际 `run(input) -> output` 边界一致：

| Agent | Required input fields | Required output fields |
| --- | --- | --- |
| Research | `query`, `search_query`, `source_items` | `search_query`, `sources`, `source_ids`, `source_snippets` |
| Summarize | `sources`, `prior_report`, `candidate_constraints`, `user_request`, `skill_prompt` | `fit_gap_report`, `evidence_notes`, `prep_plan` |
| Action | `fit_gap_summary`, `prep_plan`, `requested_action`, `user_request`, `skill_prompt` | `action_type`, `status`, `content`, `reason` |

payload 可以携带额外的派生值，但不要在 `CONTRACT` 中新增 coordinator 不会提供或公开测试 stub 不会返回的 required field。若产品确实需要扩展 handoff，应同时修改课程提供的 coordinator、测试和教学材料；这不属于本 Lab 的学生 TODO 边界。

contract 的目标是降低 agent 之间互相猜格式的概率。

### 4. 配 budget 和执行步数上限

打开：

```text
labs/lab_06/config.py
```

补：

- `max_turns`
- `max_tool_calls`
- `max_model_calls`

`max_turns` 是 scaffold 保留的配置字段名；在这个没有对话 loop 的 fixed workflow 里，它限制的是被计费的 `delegation`、`handoff` 和 `action_draft` orchestration events，不是聊天轮次，也不是 planner 反复思考的次数。`max_tool_calls` 当前只统计 Lab 6 Research 的 local `search_sources` call；`max_model_calls` 只统计三个 role-owned model boundaries。完整 Web stage 先运行的 Lab 2 structured model call，以及 Lab 4 job-board model/MCP protocol events和`tools/call`，仍在各自 trace 中，不属于这两个局部 caps。demo 时要说明每个上限实际统计什么，不能把它们泛化成整套 stage 的账单。

这些限制不是成本装饰，而是 harness 的停止条件。

### 5. 补 approval guardrail

打开：

```text
labs/lab_06/guardrails.py
```

补 `require_approval()` 的 approval / blocking branches。至少覆盖：

- direct-send request 和 external system modification 进入 `needs_approval`
- 虚构经历和 unsupported claim used in action 进入 `blocked`

`require_approval()` 是课程代码模板仓库提供的封装。课后迁到 OpenClaw、ADK、LangGraph 或其他成熟 harness 时，它可以对应到 approval callback、policy node、action confirmation 或显式 approval service。

### 6. 跑 demo

确认Lab 5 eval通过后，可以直接选择Lab 6运行；下面每次点击都会创建一个新的完整Lab 6 run：

- `lab_06_bounded_workflow`
- `lab_06_unsafe_delegation`
- `lab_06_current_openings_prep_plan`
- `lab_06_resume_bullet`

Inspector应清楚显示：

```text
coordinator delegation -> research_agent.plan_query -> Lab 1 model call
  -> research input contract_validation（含 planned search_query）
  -> Lab 4 initialize / tools/list / select_mcp_tool / optional tools/call(list_openings)
  -> research tool_call -> research_agent.run / Lab 3 search_sources -> tool_result
  -> research output contract_validation
  -> Lab 4 select_context（Skill tokens protected；keep/truncate/drop 可见）
  -> summarize input contract_validation -> handoff research → summarize
  -> summarize_agent.run -> Lab 2 FitGapReport + Lab 4 evidence
  -> summarize output contract_validation
  -> action input contract_validation -> handoff summarize → action
  -> measure_action_prompt（system instruction + 完整 action user prompt 独立预算）
  -> action_agent.run -> Lab 3 draft_action
  -> action output contract_validation
  -> require_approval
  -> model_usage / budget / stop(reason)
```

不要只看启动时“contract 已声明”。这些 contracts 约束的是每个 role 的 `run(input) -> output` 边界：实际 input/output payload 都要通过 required-field validation，缺少 required field 时，trace 应在发生失败的边界留下 failed event。Research 的 `plan_query()` 是此前单独发生的 model call；它先生成 `search_query`，该字段随后进入 Research `run()` 的 input validation。这里不声称统一校验所有字段的 type/value；例如 `FitGapReport` 仍由自己的 Pydantic schema继续验证。

降低 budget后可以观察 bounded failure；summarize 的 protected context 超出限制时，trace 必须先留下 failed `select_context` event，action prompt 超出限制时则必须先留下 failed `measure_action_prompt` event，再中止相应 model call。恢复后 stop reason必须为 `completed`。无论成功或失败，trace都要保留已经发生的 calls，不能只显示最终一句错误。

打开Diff时，Lab 5里的job-board prompt/MCP、search、context budget、draft、policy和evidence primitive应在Lab 6继续出现；其中role-owned primitives迁移到对应agent lane。Lab 3的旧source-tool selection和Lab 4的旧claim generator则由coordinator delegation与summarize agent替代，会诚实显示为Removed/Added。成功侧还必须能打开`Lab 06 call trace`，确认看到的是当前完整stage，而不是某个嵌套Lab的旧trace。分别运行outreach、prep plan和resume bullet示例，Action span必须显示不同的renderer/fallback/operation。

### 7. 用同一输入比较 Lab 5 与 Lab 6

multi-agent 的结论不能只来自“Lab 6 跑通了”。对同一份 Job Materials 和同一条 bounded request，比较：

- Lab 6 增加了哪些 role-owned model calls、handoffs、token 与延迟
- 哪些 context 被隔离，哪个 role 实际拥有哪个 tool / permission
- Research 的 raw sources 到 Summarize 后保留了什么；Summarize 的输出到 Action 时只传 `fit_gap_summary` 和 `prep_plan`，没有把 raw sources 或 `evidence_notes` 继续传下去（Action 另外接收 shared request / Skill 和 requested action type）
- handoff 的信息压缩是否足以支持 draft；如果不足，应调整 contract 或回到更简单的 single workflow，而不是让下游 agent 猜测
- evidence、policy 和最终用户可见结果是否比 Lab 5 更可靠；如果没有可验证收益，额外角色本身不算成功

CLI demo仍可运行：

```bash
uv run python -m labs.lab_06.agents.coordinator --demo
```

跑通后应该看到：

```text
agent=research
agent=summarize
agent=action
mode=live
model_calls=3
approval=blocked
trace=artifacts/lab_06/multi_agent_trace.jsonl
```

`mode=offline` 说明 `GOOGLE_API_KEY` 没配置，summary 和 draft 用的是 offline 回退文本。

## 完成后：Now you can

- 读懂并解释 coordinator 的 bounded execution plan 和停止条件
- 区分 static orchestration、search-query planning 与本 Lab 没有实现的 dynamic planning
- 判断何时 context、tool/permission 或 risk boundary 足以支持 multi-agent 拆分，何时应该保留 single workflow
- 用 explicit contracts把已有能力分配给 research/summarize/action roles
- 证明三个 role instruction 都进入各自真实 model system prompt
- 证明 summarize 和 action 两个 model input 都有各自的 context budget，并能从 trace 解释 evidence 的 keep/truncate/drop 与 action 的完整 prompt 计量
- 证明 current-opening request 仍经过 Lab 4 job-board MCP，并把岗位记录交给 summarize
- 证明 outreach、resume bullet和prep plan产生不同的 action内容形态
- 在 Inspector逐步查看 handoff input fields和下游 output
- 使用 `max_turns` 限制 orchestration step/events，并分别解释 tool/model call scope 和 stop reason
- 证明 multi-agent delegation仍遵守之前的 evidence、policy和 eval gate

## Still cannot

这个 thin harness适合学习 architecture，但自建 scheduler/runtime不是 practical成熟平台。Lab 7会把 fresh run、evidence、eval、trace和 audit组装成可演示产品；想继续做个人实用系统的学生可以走经过验收的 OpenClaw migration path。

## 给 coding agent 的边界

给 Codex / Claude Code 时，先把这些约束写进去：

- 修改范围只包括三个 agent instruction files、`guardrails.py`、`config.py`。
- 实现基于现有 thin multi-agent coordinator scaffold，不重写 scheduler。
- 先用 test failure 定位 instruction、contract、agent run output、budget 或 approval branch。
- 修改后需要能解释课程 scaffold 提供什么，agent role / contract / guardrail 由你控制什么。

## 你可以怎样自检

下面不是统一评分要求。挑对你的目标有意义的观察，判断这个 multi-agent 设计是否清楚：

- coordinator 把任务拆给 research / summarize / action 三个 agent，并调用各自的 `run()`
- 一次 `mode=live` 运行：research query、summary 和 draft 来自三个真实 Gemini 调用，trace 末尾的 `model_usage` 事件记录真实调用次数
- 各 role 的真实 input/output 都经过 required-field validation；缺字段的 failed event 指向实际 payload 边界
- trace 能看到 delegation、synthesis、action draft 和 approval decision
- orchestration step/event、tool call、role-owned model call limits 生效
- trace显示每次 Python component/operation、handoff contract和 stop reason
- unsafe action prompt 被 approval guardrail 拦住
- 输出 `artifacts/lab_06/multi_agent_trace.jsonl`

## 需要能解释清楚

用 1 分钟解释：

- 这个 thin harness 帮你练了哪些 Phase 5 决策
- 为什么这里值得拆成三个角色，以及新增调用和 handoff 的代价
- static orchestration plan、search-query planning 和 dynamic planner 有什么区别
- 课后迁到 OpenClaw 时，成熟 harness 可以接管哪些 runtime plumbing
- MCP 是 host/client 与 MCP server 之间的协议，server 暴露 tools/resources/prompts

## 重置 artifacts

```bash
uv run python scripts/reset_lab.py lab_06
```

reset 只清理本 lab artifacts；代码回滚用 git。

## 常见问题

- `mode=offline`：`GOOGLE_API_KEY` 没配置。offline 能跑通结构；要观察instruction怎样改变真实模型，再补一次 live 运行。
- agent 互相传错字段：先检查 contract，再看 coordinator trace。
- workflow 提前停止或预算超限：检查 `max_turns` 实际计数的 orchestration events，以及 tool/model call limits 和 stop condition。
- unsafe action 没被拦：检查 `require_approval()` branch 和 action type。
- 如果改动开始触及 scheduler，说明 scope 已经偏离。本 lab 基于现有 thin harness scaffold。

## 可选

- 加 reviewer agent
- 按 `optional-migrations/openclaw/` 把 Lab 6 成果迁到 OpenClaw
- 比较 OpenClaw、Google ADK、OpenAI Agents SDK 和 LangGraph 的概念映射
