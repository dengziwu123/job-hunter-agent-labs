# Lab 4: Grounded Prompts、Skills 与 MCP

## 为什么

Lab 3 已经让模型选择并调用 `search_sources`，也能产出 draft。但它仍有三个明显限制：

1. “不要编造经历”只散落在某一次 prompt 里，换一个 job-prep 任务就可能丢失。
2. 任务 prompt 没有形成可检查的学生产物；看到坏输出时，很难把问题追到某条具体指令。
3. Agent 只能搜索当前 workspace 已有材料，不能按需查询公司当前公开的职位列表。

Lab 4 不让你再写一套 retrieval、verifier 或 MCP protocol glue。你只写两个真正会进入模型输入的文本产物：

- `skills/job-prep/SKILL.md`：跨任务复用的真实性、证据和 draft-only 规则。
- `prompts/grounded-job-research.md`：本次 grounded job research 的工作流、工具使用条件和输出要求。

课程 runtime 负责加载它们、连接 MCP、执行 tool call、解析结果、核验 `source_id`，并把每一步放进网页 trajectory。这样练习重点是：**写出可执行的指令，再用 Diff 和真实 model I/O 检查它们是否产生了预期行为。**

## Before / After 到底比较什么

网页 Diff 的 Before 是完整 Lab 3，不是“Lab 4 关闭 Skill”的人工对照：

```text
Before · Lab 3
  current materials -> model chooses search_sources -> draft + action policy

After · Lab 4
  current materials
  -> load your Skill
  -> load your task prompt
  -> MCP initialize + tools/list
  -> model decides whether/how to call list_openings
  -> optional tools/call
  -> render the same task prompt with evidence + tool results
  -> structured claims
  -> Chat displays the exact model-authored claim strings
  -> course verifier records SUPPORTED / UNSUPPORTED in Inspector + evidence_report.json
```

两边都从同一份 raw materials 和同一条用户消息独立运行。After 不读取某个旧 Lab 3 artifact，也不会把 Before 的输出偷偷当输入。

Chat 只显示模型生成的 `claim` 文本，不由 adapter 添加 evidence 标题、状态标签、解释或 source 后缀。因此 Chat 中出现一条 claim 不代表 verifier 已经支持它。要看证据 verdict，请在 Harness Inspector 展开 `build_evidence_notes()` 的 `evidence` event，或打开本轮的 `Evidence report` artifact；其中的 `EvidenceNote.status`、`source_id` 和 `supporting_snippet` 才是课程 verifier 的结果。

## 课程已经提供什么

你不用修改这些文件：

- `src/retrieval.py`：复用 Lab 3 `search_sources()` 和 `SourceResult`。
- `src/evidence.py`：检查模型声明的 `source_id` 是否存在、snippet 是否真的支持 claim。
- `src/context_budget.py`：真实输入超过上限时才做安全裁剪，并记录 keep/truncate/drop；本 Lab 不制造一个小预算来强迫 compression。
- `src/prompt_loader.py`：检查并替换 task prompt 的 placeholders。
- `src/claim_generation.py`：把 rendered prompt 发给当前 provider，并保存 exact input/output。
- `src/mcp_client.py`：根据 server 声明选择工具、构造参数、解析 content blocks。
- `src/job_research.py`：把 student prompt、模型 tool decision 和 MCP session 串起来。
- `src/job_board_server.py`：把 Greenhouse、Lever、Ashby 的公开 job-board API 规范化成一个 MCP contract。
- `src/mcp_client_adapter.py`：保留一个把 Lab 3 source tool 放到 MCP 后面的可读示例，供后续 Lab 和 CLI 查看。

你也不用“安装 Skill”。`skill_loader.py` 会从当前 workspace 直接加载这份本地 `SKILL.md`。这里学习的是如何设计、组合和观察 Skill，不是发布 Skill 包。

## TODO 1：写 reusable Job Prep Skill

打开：

```text
labs/lab_04/skills/job-prep/SKILL.md
```

保留 frontmatter，替换四处 `TODO(lab_04)`。所有真正的规则写成 `- ` bullet，因为 loader 会把这些 bullets 放进模型 prompt。

至少覆盖：

- 何时使用：fit/gap、resume bullets、outreach draft、interview prep。
- Candidate truth：不得把岗位要求、模型记忆或愿望写成候选人经历。
- Evidence：事实 claim 必须引用本轮提供的 exact `source_id`；缺证据就明确 unsupported。
- Company truth：公司/职位事实只能来自 job description、workspace Web source 或 MCP result。
- Draft-only：Skill 本身不能投递、发邮件、修改外部系统；外部动作仍需 human approval。

Skill 应当是可跨任务复用的 policy，不要把“Stripe”“Kubernetes”或某次问题写死在里面。

## TODO 2：写 grounded job-research task prompt

打开：

```text
labs/lab_04/prompts/grounded-job-research.md
```

保留下面五个 placeholders；课程 runtime 会在每次 run 中替换它们：

| Placeholder | Runtime 填入什么 |
| --- | --- |
| `{{skill_rules}}` | 你在 `SKILL.md` 中写的 reusable rules |
| `{{user_request}}` | 当前 bounded user request |
| `{{evidence_sources}}` | 当前 profile、JD、Lab 3 Web sources 与 MCP records 的 exact `source_id` / snippet |
| `{{available_tools}}` | MCP `tools/list` 返回的 tool descriptions 与 input schemas |
| `{{job_openings}}` | 通过 evidence budget 的 MCP 职位记录 `source_id`；完整内容只在 `{{evidence_sources}}` 出现一次，没有调用时为空 |

task prompt 至少讲清楚：

- 什么请求需要查 current openings，什么请求不需要；不要“有 tool 就一定 call”。
- current-opening 请求使用 `list_openings`；普通 evidence 核验不要为了展示 tool 而调用。
- 如何比较 role requirement 与 candidate evidence，尤其是“JD 有要求、candidate profile 没有”的情况。
- 如何返回 schema 所需的短 factual claims 与 `source_id`；缺证据时 `source_id` 留空。
- 不把 job requirement 改写成 candidate experience，不在 prompt 中批准外部行动。

Skill 与 task prompt 不应重复整段内容：Skill 是长期规则，task prompt 是这次 workflow 与 output contract。

## MCP：学生不用写，但必须读懂的完整调用链

### 1. Client 连接 server 并执行 `initialize`

网页路径用官方 MCP SDK 创建 client 和 course-provided `job_board` server。两者在同一 Python process 内连接，方便 macOS、Windows 和 CI 稳定运行。

`initialize` 仍是真实协议 handshake，不是一个叫 `ping()` 的普通函数。client 与 server 在这里协商：

- MCP protocol version；
- server name/version；
- server 声明支持哪些 capability，例如 tools、resources、prompts。

“in-process”描述的是 transport/deployment；`initialize`、schema 和 message contract 仍是 MCP。

### 2. Client 调用 `tools/list`

`job_board` server 声明两个工具：

```text
list_openings(company, ats="greenhouse", limit=20)
get_opening(company, job_id, ats="greenhouse")
```

SDK 根据 Python tool signature 生成 JSON Schema。trajectory 会显示 tool name、description、`properties`、`required`。client 不能凭记忆猜参数；`build_arguments()` 只保留 schema 声明的字段，并拒绝缺失 required argument。

MCP server 还可以暴露 `resources` 和 `prompts`。课程的 `job_prep_sources` 示例同时演示 `resources/list` 与 `prompts/list`；job-board 主路径只需要 tools。不要把 MCP 误解成“只有 function calling”，也不要为了展示 capability 强行调用本任务用不到的接口。

### 3. Tool declarations 与你的 prompt 一起进入模型

课程把 `tools/list` 的 declaration 填进 `{{available_tools}}`，同时把同一 schema 作为 provider tool declaration 发送。Inspector 中 `select_mcp_tool` 的 `model_io.actual_provider_input` 会显示真正发送的 task prompt 与 tool schema。

这次 tool-selection model call 也受 4,000-token 输入上限保护，不会把 Lab 3 找到的所有长材料先无界塞进模型、等后面的 claim generation 才裁剪。`select_mcp_tool.details.input_budget` 会记录原始估算、实际提交估算以及 evidence 是否因真实超限而截短；正常 fixture 不会为了演示而强制截断。最终 claim-generation prompt 在加入 job records 后会再独立做一次 source-level keep/truncate/drop，因为两次模型调用的输入结构不同。职位的完整 title/location/summary 只作为 evidence 参与这一次预算；`{{job_openings}}` 只列出最终保留下来的 `source_id`，不会让已 drop 或 truncate 的原始记录从另一个 placeholder 绕过预算。

此时是模型做决定：

- 请求只是在核验已有经历：可以不 call job board。
- 请求要求查询某公司当前职位：选择 `list_openings` 并给出 `company`、`ats`、`limit`。

离线 CI 没有 API key，课程使用明确标记为 `offline_fallback` 的确定性 decision；它不会伪装成模型输出。live 模式则记录 `decision_source=model`。

### 4. Host 执行 `tools/call`

模型提出 tool call 不等于模型自己访问网络。host 仍会：

1. 找到 `tools/list` 中同名 tool；
2. 按 schema 过滤并验证 arguments；
3. 发送 MCP `tools/call`；
4. 检查 result 是否为 error；
5. 把 records 交回后续 prompt。

网页默认使用 bundled ATS response，保证 Diff 与 tests 可重复，并在事件中标明 `data_mode=bundled_fixture`。要看公开 board 的即时数据可运行：

```bash
uv run python -m labs.lab_04.src.mcp_client \
  --server jobs --company stripe --ats greenhouse --limit 5
```

这个命令不加 `--offline` 时访问公开 ATS API；不需要 LLM key。课程没有连接 LinkedIn：它没有适合本课的官方公开接口，不能把 session-cookie scraping 当作安全的 job-research capability。

### 5. Client 解析 tool result

MCP tool result 是一组 `content` blocks；`structured_content` 是 server/SDK 可能额外提供的便利字段，不保证存在。

本课程的 `records_from()` 先检查：

- `result.is_error`：server 已经跨协议返回失败；
- `structured_content`：有就读结构化 payload；
- 否则从 text content block 解析 JSON。

只读取 `structured_content` 的 client 可能在 `get_opening` 上得到空结果。Inspector 会显示 content-block types、是否存在 structured content、record count。

### 6. 为什么有 `list_openings` 和 `get_opening`

`list_openings` 返回 title、location、URL 和最长 400 字符的 summary；`get_opening` 才返回一个职位的全文。这是 server 提供的 progressive-disclosure capability：先用短列表找候选职位，需要具体要求时再取一个 detail。

这不是为了“完成一个 compression TODO”而随便截成任意固定长度。真实 ATS board 可能有大量职位和很长 HTML；API 在 capability boundary 上分 list/detail，避免一次把整站塞进 model context。本 Lab 必做的网页路径只让模型决定是否调用 `list_openings`，不会自动调用 `get_opening`；你可以通过 CLI/client 手动读取 detail，或把自动 detail call 当作课后扩展。

### 7. 错误如何出现在 trajectory

每个完成的 protocol step 都单独记录 timing。如果 `tools/call` 失败，前面已经成功的 `initialize`、`tools/list` 和 model decision 不会消失。失败事件保留：

- 失败 operation；
- server/tool/arguments；
- 已完成 operations；
- error result 或 transport exception 的身份。

因此你能区分 prompt 没让模型选 tool、schema/argument 错误、server 返回 error、transport 失败，以及后续 claim verifier 拒绝证据。

## 用网页验证你的 prompt，而不是只看最终答案

完成两个 TODO 后：

```bash
uv run pytest labs/lab_04/tests
uv run python -m labs.lab_04.src.run_evidence_report --use-skill
uv run python scripts/check_example_answers.py --lab lab_04 --main
uv run python -m labs.shared.web.app
```

打开网页，Lab 4 有三个 examples：

1. Fake experience：材料没有 production multi-agent migration。After Chat 保留模型生成的 claim 文本；在 Harness Inspector 的 `evidence` event 或 `Evidence report` artifact 中，这条 claim 必须是 `unsupported`，且不能造 `source_id`。
2. Missing requirement：JD 要 Kubernetes 但 profile 没写，After 应把它表达为 gap，而不是候选人经历。
3. Current openings：模型应选择 `list_openings`；trajectory 显示 MCP handshake、schema、exact prompt、arguments 和 records。

### 在 Diff 里看到症状，回去改哪一句

这三个 example 的作用不是"跑通",是**把 prompt 的漏洞变成看得见的输出**。看到左边,就去补右边:

| Diff / trajectory 里看到 | task prompt 里缺的那句 |
| --- | --- |
| Inspector 的 `evidence` event 或 `Evidence report` 中，`unsupported` claim 却带着一个 `source_id` | 缺证据时 `source_id` 必须留空 |
| JD 的要求被写成候选人已有的经历 | 不得把 job requirement 改写成 candidate experience |
| 请求只是核验经历,却调用了 `list_openings` | 什么请求需要查当前职位、什么不需要 |
| 请求要查当前职位,模型却没调工具 | 同上;条件写得太含糊模型不会触发 |
| claim 数量或结构不符合 schema | 输出契约:几条、多短、每条要带什么 |
| Inspector / artifact 中所有 claim 都是 `unsupported` | 要求模型引用**本轮提供的** exact `source_id` |

反过来也成立:**如果你把某一句从 prompt 里删掉,对应的症状应该出现。** 出现不了,说明那句话本来就没起作用——那它不该留在 prompt 里占 token。

另外,runtime 会挡住"只有 placeholder、没有指令"的 prompt:

```text
The Lab 4 task prompt has no instruction of your own.
```

这只检查你没有提交一个空壳，不检查字数或语言，更不是质量评分。**prompt 能不能工作靠的是上面那张表和实际 Diff，不是长度。**

做一次小型 prompt experiment：只改 task prompt 中一条可验证指令，例如“没有 candidate evidence 时必须明确写 missing support”，保存后用同一个 example 重跑。比较：

- Diff 的 final output（这里只显示模型 claim 文本，不显示 verifier status）；
- `load_task_prompt` 中记录的 template；
- `select_mcp_tool.model_io.actual_provider_input`；
- `generate_claims.model_io.actual_provider_input` 与 structured output；
- Harness Inspector 和 `Evidence report` artifact 中的最终 evidence notes。

如果 output 没变，也要能从 trajectory 判断：是新指令没进入 prompt、离线 fixture 不受语义变化影响、模型忽略了它，还是 verifier 把变化挡住了。**看到因果链比“prompt 看起来写得不错”更重要。**

## 抽查会看什么

- Starter 中只有 `SKILL.md` 与 `grounded-job-research.md` 是学生 TODO；不得要求学生补 `select_context`、`build_evidence_notes`、`pick_tool` 或 `build_arguments`。
- Skill rules 与 task prompt 都真实进入 model input，并在 trajectory 中可见。
- task prompt 保留五个 placeholders，能区分 reusable policy 与 task workflow。
- fake experience、missing requirement、current openings 三个 examples 都能运行。
- current-openings 路径按实际顺序显示 `initialize -> tools/list -> select_mcp_tool -> tools/call`。
- MCP client 同时处理 content blocks 与 optional `structured_content`，Windows/macOS 都通过。
- 不通过制造任意小 token budget 或固定字符 slice 来演示 compression。
- 不修改 Lab 1–3 的学生 TODO。

## 完成后：Now you can

- 写一个可跨 job-prep 任务复用的本地 Skill。
- 写一个 task-specific grounded prompt，并证明 exact prompt 进入了模型。
- 用 Diff 与 trajectory 判断 prompt、tool decision、tool result 和 verifier 各自造成了什么变化。
- 读懂一个 course-connected MCP client 从 handshake 到 result parsing 的完整路径。

## Still cannot

三个 example 只能说明行为在少数 case 上合理，不能证明 Agent 在一组任务上稳定。Lab 5 会把这个真实 Lab 4 behavior 当作 eval target，定位 regression 和 first failure point。
