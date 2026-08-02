# Lab 3: Tools + Action Boundary

Phase: **Phase 3A: Connecting the Agent to the Real World**

## 为什么现在要加 conversation state、tool 和 action policy

Lab 2 已经能把**当前这一轮**变成结构稳定、可验证、可保存的 task snapshot，但下一次请求仍然是一个新的 one-shot run：它不会自动读取上一轮对话，也不会把上一轮 report 当成正在继续的任务。

Lab 3 从这里开始增加真正的 multi-turn continuity：同一个 Lab 3 workspace 的下一轮会读取上一轮经过验证的 `ManagedTaskState`，同时把当前 stage 中有界的 user request history 交给模型。上一轮 assistant response 和 report 不会被原样塞回模型；state 只提供任务 identity、revision、source 和 validation 等受控 metadata。它不会读取某次 Lab 2 的 output artifact；Lab 3 只是复用 Lab 2 的结构化能力代码，在自己的 stage run 里构造、验证并推进自己的 state。

Lab 3 还会加入 Web source、tool calling 和 action policy。切换到 Lab 3 后 UI 会出现 `Public job or company webpage`；你只需要提供 URL，课程 scaffold 会在 Agent run 中调用 `fetch_web_page()`，把可读正文保存成 Web source。你不需要写爬虫。模型随后选择是否调用 `search_sources`，而 action 的状态由代码决定。

## 目标

让 Gemini 能继续上一轮任务，再完成一次真实的 source-search tool call：模型读取有界的 user request history 和上一版 validated state metadata，自己决定怎样调用 `search_sources`，读完课程 fetcher 生成的 Web source 后起草 outreach 文本。所有外部动作仍然只停留在草稿阶段。本节先把 state、tool 和 action 的边界讲清楚，Lab 6 再进入课程提供的 thin multi-agent scaffold。

这节主要看三件事：

- **multi-turn state 是 Harness 的决策**：代码加载上一版 state、保持同一个 `state_id`、递增 `revision`，并且只在本轮完整验证成功后覆盖持久化状态
- **tool calling 是模型的决策**：不是脚本硬编码调用顺序，而是模型看到 tool schema 后决定查什么、查几条（课程提供的 `model_tool_use.py` 封装了这个 function calling 流程）
- **action boundary 是代码的决策**：模型可以草拟，但系统不能真实 send、apply、publish；风险动作必须进入 approval path
- **网页抓取是课程 plumbing**：HTTP、redirect、HTML清理、1 MB上限和私网地址拦截已经提供，学生只运行、观察和解释，不实现 scraper

没有配置 `GOOGLE_API_KEY` 时，demo 会以 `mode=offline` 用确定性 stub 跑通流程；本 lab 的验收要求至少一次 `mode=live` 运行。

## 本 lab 的产出

完成后会生成：

```text
artifacts/lab_03/tool_trace.jsonl
artifacts/lab_03/runs/<run_id>/tool_action_run.json
artifacts/task-state/lab_03/<workspace_id>/task_state.json
```

trace 里要能看到 tool call、tool result、draft action 和 approval decision。

## 修改范围

只改这些文件：

```text
labs/lab_03/src/tools.py
labs/lab_03/src/policies.py
labs/lab_03/src/state_store.py
```

这些文件不改：

```text
labs/lab_03/tests/
labs/lab_03/data/
labs/shared/
```

`run_with_tools.py` 和 `model_tool_use.py` 可以阅读，用来理解模型如何驱动 tool call；默认不需要修改。

## 已经搭好的部分

代码模板已经准备好：

- synthetic Web source fixtures
- protected public webpage fetcher（静态 HTML/plain text；不执行 JavaScript）
- Gemini function calling 封装（`model_tool_use.py`，无 key 时自动降级 offline stub）
- trace writer
- unsafe action prompts
- Lab 3 tests

你要补的是 managed state revision、tool schema、result mapping 和 draft action policy。

## 任务

### 1. 先跑 deterministic unit tests

```bash
uv run pytest labs/lab_03/tests
```

本 lab 的测试不依赖真实外部服务。失败时，先判断问题出在 tool schema、policy branch，还是 trace shape。

运行初始代码时，state、tool 和 policy 对应的 tests 会失败。其中一个 test 会连续推进两版 state；另一个会把不同的 Web source 直接传给 `search_sources()`；adapter test 用 fake public webpage 验证完整流程。测试不访问真实互联网，硬编码 fixture answer 也不会通过。

### 2. 补 `advance_task_state()`

打开：

```text
labs/lab_03/src/state_store.py
```

完成 state transition：

- 第一轮创建 `revision=1` 和新的 `state_id`
- 后续轮先验证 previous state，保持相同 `state_id`，然后把 `revision` 加一
- 只追加当前 user request；report、sources、action status 和 run id 更新为本轮验证后的值
- 不能把 UI transcript 当成 persisted state；只有有界的 user request history 进入模型上下文，`ManagedTaskState` 是 Harness contract

### 3. 补 `search_sources()`

打开：

```text
labs/lab_03/src/tools.py
```

完成 `search_sources()`：

- description 写清楚输入是什么
- 输出映射成稳定结构
- 每条 result 保留 `source_id` 或 path，给 Lab 4 evidence 用
- `source_items`存在时搜索当前 UI workspace；只有 CLI未传入时才读取课程 fixture

不要修改 `labs/shared/web/web_fetch.py`。这门入门课不想把重点变成爬虫；public tests 会单独验证它的安全和解析边界。

### 4. 补 `draft_action()`

打开：

```text
labs/lab_03/src/policies.py
```

完成 action policy：

- outreach、resume bullet、prep plan 可以生成 draft
- send/apply/publish/update external system 进入 `needs_approval`
- 虚构经历或没有支持证据的 action 进入 `blocked`
- 不接真实发送、投递或发布 integration

### 5. 跑 demo

先确认 Lab 2代码和tests已经完成，然后可以直接在UI运行Lab 3：

1. 切到 Lab 3，在左侧添加一个公开职位页或公司网页 URL（也可以保留 synthetic Web source）。默认synthetic profile/JD不会替你排入公网URL；要观察fetch差异必须先完成这一步
2. 运行 `lab_03_grounded_draft`；URL会在这次 Agent run里从 `Waiting`变成 `Ready`，state 是 revision 1
3. 运行 `lab_03_stateful_followup`；确认 `state_id` 不变、revision 变成 2，而且模型上下文包含第一轮 user request 和上一版 managed-state metadata
4. 再运行 `lab_03_unsafe_send`

Lab 3 第一轮从当前 materials 建立 structured foundation；后续轮同时读取当前 materials、有界的当前 stage user-request history，以及上一版 Lab 3 managed state 的 identity/revision/source/action metadata。上一版模型报告不会被重新塞进生成新报告的 prompt；切换到其他 stage 也不会把那个 stage 的 output 当成 Lab 3 input。

Inspector成功顺序：

```text
load_task_state()
  -> ModelClient.complete()
  -> validate_fit_gap_report()
  -> JobPrepState.mark_report_generated()
  -> fetch_web_page()                 # 只有 queued URL时出现；课程提供
  -> MaterialStore.complete_web_source()
  -> ToolUseModel.request_tool_call()
  -> search_sources()
  -> ToolUseModel.draft_outreach()
  -> classify_action()
  -> advance_task_state()
```

这里 `fetch_web_page` 是课程提供好的底层工具基建（tool plumbing），`request_tool_call` 是模型决策，`search_sources` 和 `classify_action` 是 Harness 执行的部分。trace 要明确显示 requested/final URL、source ids、draft size、action type、status 和 reason。

CLI demo仍可单独运行：

```bash
uv run python -m labs.lab_03.src.run_with_tools --demo
```

跑通后应该看到：

```text
tool=search_sources
mode=live
tool_call_source=model
model_calls=2
draft_status=draft_created
action_status=needs_approval
trace=artifacts/lab_03/tool_trace.jsonl
```

三种情况要分清：

- `mode=offline`：`GOOGLE_API_KEY` 没配置，整个流程在用确定性 stub。
- `mode=live` 但 `tool_call_source=fallback`：模型被调用了，但它没有发起 function call，runner 退回了默认 query。这不算完成本 lab 的核心验收——通常是 tool description 不够具体，改完 `search_sources()` 的 description 再跑。
- `mode=live` 且 `tool_call_source=model`：tool query 真的是模型决定的，这才是验收要的状态。

`draft_status` 偶尔会是 `blocked`——说明模型起草的文本触发了你的 policy 关键词，这正是 policy 在工作；看一眼 trace 里的 draft 内容确认即可。

## 完成后：Now you can

- 让 Agent读取公开职位页或公司网页，并留下明确 fetch trace
- 让模型根据 user request选择 Web source query
- 把网页正文映射成稳定 `SourceResult`
- 生成 local draft，并用代码返回 `draft_created`、`needs_approval`或 `blocked`
- 在同一个 Lab 3 workspace 继续多轮任务，并用 `state_id + revision` 看见明确的 state transition

## Still cannot

抓到一个网页并返回 snippet，不代表网页可信，也不代表它真的支持生成的每一个 factual claim。Lab 4会把这些 Web sources提升为待验证的 Evidence sources，并加入 claim-level verifier；学生不需要实现 retrieval或 RAG。

## 给 coding agent 的边界

给 Codex / Claude Code 时，先把这些约束写进去：

- 修改范围只包括 `state_store.py`、`tools.py` 和 `policies.py`。
- 不新增真实 send/apply/publish integration。
- 先用 test failure 定位 tool schema、policy branch 或 trace event。
- 修改后需要能解释哪段代码创建 `DraftAction`，哪段代码只记录 trace。

## 抽查会看什么

准备好展示：

- 一次 `mode=live` 且 `tool_call_source=model` 的运行：trace 的 `tool_call` 事件里 `source=model`，query 是模型自己选的
- 模型起草的 outreach 进入 `DraftAction(status="draft_created")`，send 请求进入 `needs_approval`
- trace 写入 `artifacts/lab_03/tool_trace.jsonl`
- direct-send prompt 不会产生真实外部副作用
- UI显示来自当前 workspace的 source ids，以及与Lab 3同一个run id的Lab 2 schema/state events
- queued URL的 Inspector明确显示 `labs.shared.web.web_fetch.fetch_web_page()`，而不是声称学生实现了 scraper
- 能解释 tool call 和 action boundary 的区别：哪些决定是模型做的，哪些是代码做的

## 重置 artifacts

```bash
uv run python scripts/reset_lab.py lab_03
```

reset 只清理本 lab artifacts；代码回滚用 git。

## 常见问题

- `mode=offline`：`GOOGLE_API_KEY` 没配置。offline 能跑通结构，但验收要求 live 至少一次。
- `tool_call_source=fallback`（live 模式下）：模型没发起 function call，runner 退回了默认 query。先检查 tool description 是否具体、参数说明是否清楚；fallback 的运行不满足验收。
- action status 错误：先看 policy branch，再看 unsafe prompt fixture。
- trace 缺字段：对照 tests 期待的 event shape。
- URL抓取失败：很多招聘网站使用登录、反爬或 JavaScript。换官方静态页面，或继续使用 synthetic Web source；本课不要求绕过限制。
- 如果实现开始引入 Gmail、LinkedIn 或真实 API 调用，说明 scope 已经偏离。本 lab 只保留 draft-only action。

## 可选

- 比较 bad tool schema 和 improved tool schema
- 加 deterministic transform tool
- 写一个 30 行以内 mini dispatch loop，只用于理解成熟 harness 通常封装了什么
