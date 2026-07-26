# Lab 2: Structured Output + State

Phase: **Phase 2: Prompt Engineering**

## 为什么现在要加结构和 state

回到 Lab 1，连续运行两次 `lab_01_fit_gap`。模型通常能给出有用建议，但 `strengths`、`gaps`、`missing_info` 可能改名、缺失，或者混在一大段文字里。于是你的 Python 无法放心地执行 `response["missing_info"]`。浏览器里还能看到上一条消息，也不代表 runtime 已经保存了一份经过验证的任务状态。

Lab 2 保持同一份 Job Materials 和 Lab 1 的模型调用边界，只加入 schema validation 和 state transition。这样你看到的提升，确实来自这两个 Harness component，而不是因为换了一个 chatbot。

## 目标

把 Lab 1 的普通文本回复，改造成代码可以消费的 `FitGapReport`，并把一次 job-prep 任务的 state 保存下来。

这节不比谁的 prompt 写得更长，只看输出能不能稳定地交给代码：

- 模型输出能通过 schema 校验
- 格式错误的输出不会静默进入系统
- profile、JD、report 和 task status 能落到 state 里

## 本 lab 的产出

完成后会生成：

```text
artifacts/lab_02/job_prep_report.json
artifacts/lab_02/runs/<run_id>/trace.jsonl
```

里面要包含一个合法 `FitGapReport`，以及生成这个 report 时使用的 profile、JD、state status 和 validation status。

## 修改范围

只改这些文件：

```text
labs/lab_02/src/schemas.py
labs/lab_02/src/state_store.py
labs/lab_02/src/run_structured.py
```

这些文件不改：

```text
labs/lab_02/tests/
labs/lab_02/data/
labs/shared/
```

## 已经搭好的部分

代码模板已经准备好：

- synthetic `CandidateProfile`
- local `JobDescription`
- malformed fixture，用来测试 schema validation
- artifact writer
- Lab 2 tests

你要完成 schema、state update，以及 structured Gemini client 的连接部分。

## 任务

### 1. 先跑 unit tests

```bash
uv run pytest labs/lab_02/tests
```

运行初始代码时，你会看到 `5 failed, 1 passed`。失败分别覆盖 Lab 1 model boundary 的复用、`FitGapReport` 字段、state transition、CLI wiring 和 UI adapter。不要修改 tests，也不要把 extra fields 设成 silently ignore 来消除失败。

### 2. 补 `FitGapReport` schema

打开：

```text
labs/lab_02/src/schemas.py
```

`FitGapReport` 至少要表达这些信息：

- `fit_summary`
- `strengths`
- `gaps`
- `risks`
- `missing_info`
- `recommended_next_steps`

字段命名以模板和 tests 为准。先别急着把文字写漂亮，先保证下游代码每次都能稳定读到这些字段。

### 3. 补 state update

打开：

```text
labs/lab_02/src/state_store.py
```

先完成一条最小的 state transition：

```text
started -> report_generated
```

state 里至少要能追踪：

- selected profile
- selected JD
- latest report artifact path
- validation status

### 4. 接 structured run

打开：

```text
labs/lab_02/src/run_structured.py
```

`GeminiStructuredReportClient` 已经持有 Lab 1 的 `ModelClient`。你要构造包含 required fields 和 typed profile/JD 的 messages，调用 `self.model_client.complete()`，把返回的 assistant text 解析成 JSON object，再交给 `FitGapReport` 校验。不要在 Lab 2 直接 import 或调用 `google.genai`。否则你会复制 Lab 1 的 model boundary，前面搭好的结构就断掉了。

schema validation 是运行时的刚性保证（runtime guarantee），不是靠正则去猜自由文本。非 JSON、缺字段、错误类型和 extra fields 都必须失败，不能生成“看起来成功”的 state。

格式错误的测试数据（malformed fixture）是用来测试系统重试机制或验证错误处理路径的，绝不能被当成合法的 report 保存下来。

单测会注入一个固定的 Lab 1 model client，不调用 Gemini。它会检查 system/user messages 和 profile/JD ids，证明你的代码确实复用了前一层。实际 UI 和 CLI 才会使用你在 Lab 1 完成的真实 Gemini implementation。

### 5. 跑完整流程

先在一直运行的 UI 选择 `Lab 2 · Structured Output & State`，运行 `lab_02_fit_gap`。再切回 Lab 1，运行完全相同的 prompt。

Lab 2成功后 Inspector必须按顺序显示：

```text
ModelClient.complete()
  -> validate_fit_gap_report()
  -> mark_report_generated()
```

展开 events，确认 response fields、schema status、`started -> report_generated` 和 portable artifact path。Chat 显示的是给人读的格式；Inspector 中的 `report` 和 `task_state` 才是代码真正使用的对象。

CLI仍然可以独立运行：

```bash
uv run python -m labs.lab_02.src.run_structured --track job_hunting
```

跑通后应该看到：

```text
track=job_hunting
status=report_generated
artifact=artifacts/lab_02/job_prep_report.json
```

## 完成后：Now you can

- 从相同 profile/JD得到严格的 `FitGapReport`字段和类型
- malformed output在进入业务 state前失败
- 保存 selected profile、JD、validation status和 report artifact

## Still cannot

Lab 2只能使用已经粘贴/上传的 resume和 JD。给它一个职位页或公司网页 URL，它不能主动打开；它也没有把“生成 draft”和“真的 send/apply”分开。Lab 3会复用当前 profile/JD/report contract，由课程 scaffold负责网页读取，再让学生实现 source search和 action policy。

## 给 coding agent 的边界

给 Codex / Claude Code 时，先把这些约束写进去：

- 修改范围只包括 `schemas.py`、`state_store.py`、`run_structured.py`。
- 不改 tests、fixtures、golden outputs 和 `labs/shared/`。
- 先用 test failure 定位 TODO，再改 schema、state 或 structured client wiring。
- 修改后需要能解释 task state 和 transcript 的区别。

## 抽查会看什么

准备好展示：

- 同一个 synthetic profile + JD 能生成合法 `FitGapReport`
- artifact 写入 `artifacts/lab_02/job_prep_report.json`
- 修改一次用户信息后，state 能保存更新
- malformed fixture 不会静默通过
- UI中同一 prompt对比 Lab 1和 Lab 2
- Inspector能解释三次 Python call及 state transition
- 能解释 structured output 和普通聊天回复的区别

## 重置 artifacts

```bash
uv run python scripts/reset_lab.py lab_02
```

reset 只清理本 lab artifacts；代码回滚用 git。

## 常见问题

- schema validation 一直失败：先打印 raw model output，确认字段名和类型。
- state 没更新：检查 run flow 是否在 validation 通过后才写 state。
- artifact 为空：检查 artifact path 和 JSON serialization。
- tests fail after Codex edits：检查是否越过修改范围。

## 可选

- 比较两个 prompt variants
- 加更细的 state transition tests
- 做 Personal Learning Coach schema
