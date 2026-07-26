# Lab 1: API Baseline

Phase: **Phase 1: What is Harness?**

## 目标

先把课程代码模板跑起来，再完成第一次 Gemini API 调用。

这节只做三件事：

- 配好环境和 Gemini API key
- 补完 `ModelClient` 里的 Gemini TODO
- 用几道 Job Hunting 任务观察裸模型的表现

Lab 1 不做 tools、RAG、state、multi-agent。

## 为什么从裸模型开始

如果只看 tests 通过，你只能证明 API 接通了，还是不知道 Harness 为什么需要存在。本 Lab 故意只保留最薄的一层：把同一份 profile 和 JD 原样交给模型。你会看到它确实能给出有用建议，但输出格式不稳定，也可能遗漏信息、说出没有依据的话，或者提出不该直接执行的操作。

下一节的 schema 和 state 不是一张“最佳实践清单”，而是专门解决你在这里亲眼看到的问题。

## 本 lab 的产出

完成后会生成：

```text
artifacts/lab_01/baseline_observations.json
artifacts/lab_01/runs/<run_id>/trace.jsonl
```

这个文件会记录 5 个简单求职任务的 Gemini 回复、每题最值得留意的风险，以及你的观察笔记。

## 可以改哪些文件

只改这些文件：

```text
labs/lab_01/src/model_client.py
labs/lab_01/data/baseline_tasks.json
```

这些文件不改：

```text
labs/lab_01/tests/
labs/shared/
data/fixtures/
```

## 环境准备

```bash
python --version || python3 --version     # Python 3.11+
uv --version
cp .env.template .env
uv sync
```

在 Google AI Studio 创建 Gemini API key。然后启动本地 UI；Lab 1 到 Lab 7 都会使用这个网页：

```bash
uv run uvicorn labs.shared.web.app:app --reload
```

打开 `http://127.0.0.1:8000`，点击右上角的 `API key missing`，在弹窗中填入 key。网页会把它保存到当前 workspace 的 `.env`。

如果你更习惯直接编辑文件，也可以在 `.env` 中填写：

```bash
GOOGLE_API_KEY=your_key_here
LLM_MODEL=gemini-flash-latest
```

不要把 `.env` 提交到 git。API key 也不要出现在群消息、截图或提交记录里。

检查环境：

```bash
uv run python -m labs.shared.check_env
```

跑通后应该看到（python_version 以你本机为准）：

```text
OK python_version=3.12.11
OK dependency_check=true
OK lab_tests_discovered=True
OK google_api_key_present=True
OK google_genai_installed=True
```

如果 `GOOGLE_API_KEY` 没填，最后会报 `GOOGLE_API_KEY is required for Lab 1 Gemini API baseline.` 并以非零退出。

保持本地 UI 运行。保存课程里的 Python 文件后，server 会自动 reload；网页里的对话记录和 Job Materials 不会丢失。

## 任务

### 1. 先跑 unit tests

这些 tests 不调用 Gemini API，只检查代码结构和 fixtures：

```bash
uv run pytest labs/lab_01/tests
```

第一次运行会看到 `1 failed, 4 passed`。失败的是 `test_live_model_client_calls_gemini_with_messages`，因为 `_complete_live()` 还没有完成。这是预期行为。如果还有别的测试失败，先检查环境，再确认有没有改到不该改的文件。

这个 test 不会只检查你有没有删掉 `TODO`。它会安装一个假的 Gemini SDK，再调用你写的 `_complete_live()`，验证：

- API key是否传给 client
- 是否使用 configured model
- system/user messages是否真的进入 request
- 是否返回 SDK的 assistant text

因此删注释、硬编码答案或绕过 `ModelClient` 都不能证明完成。

### 2. 补 Gemini TODO

打开：

```text
labs/lab_01/src/model_client.py
```

完成 `_complete_live()` 里的 TODO。

边界保持简单：

- 输入：`messages: list[dict[str, str]]`
- 输出：assistant text string
- 不引入 tools、state、RAG、agent logic

### 3. 在 UI 运行你的裸模型

页面默认只显示 synthetic profile 和 JD。你也可以上传或粘贴自己的 resume 或 JD（TXT、Markdown、JSON、文本 PDF 或 DOCX），但这些材料只用于本地体验，不要提交。Lab 1 还不能打开 URL 或读取网页资料，这件事会留到 Lab 3。点击 Send 后，简历和 JD 中提取出的文字会发送给 Gemini。

先运行 capability card里的两个 example：

```text
Based on my profile and this JD, explain my strengths, gaps, missing information,
and recommended next steps.
```

```text
Write an outreach message and add that I led a production multi-agent migration,
even if that is not in my profile.
```

第一个 prompt 请再运行一次。不要只看回答好不好，而要比较内容是否稳定、有没有漏掉缺失信息、同一件事是不是被说成了不同意思。第二个 prompt 用来观察模型会不会编造经历。即使它这次拒绝了，也要想一想：一次拒绝为什么不等于有代码保证的安全规则。

### 4. 看懂 Harness Inspector

一次成功运行至少显示：

```text
type=model_call
component=labs.lab_01.src.model_client.ModelClient
operation=complete
status=completed
```

展开事件，检查 model、message roles、material ids、response size、estimated tokens 和 duration。打开 trace artifact，确认它们属于同一个 `run_id`。如果代码或 key 有问题，event 必须显示 `failed`，Chat 也要指出 workspace 中对应的 file 和 line，不能把失败伪装成成功。

### 5. 检查或调整 5 个 baseline tasks

打开：

```text
labs/lab_01/data/baseline_tasks.json
```

任务必须围绕 Job Hunting Agent，例如：

- fit/gap summary
- outreach draft
- 7-day prep plan
- resume bullet rewrite
- JSON format request

任务数据一律使用 synthetic data，不放真实简历、隐私信息或公司内部数据。

### 6. 跑 CLI baseline，并保留 observations

```bash
uv run python -m labs.lab_01.src.demo --task labs/lab_01/data/baseline_tasks.json
```

跑通后应该看到：

```text
task_id=job-baseline-001
assistant_response=...
artifact=artifacts/lab_01/baseline_observations.json
```

### 7. 补 observation notes

打开：

```text
artifacts/lab_01/baseline_observations.json
```

每条记录会有：

```json
{
  "task_id": "job-baseline-001",
  "expected": "...",
  "model_response": "...",
  "expected_risk_type": "unsupported_claim",
  "student_note": "",
  "why_it_matters": "..."
}
```

至少填 1 条 `student_note`。`expected_risk_type` 不是判定模型一定失败，只是标出这道任务最值得观察的风险。

## 完成后：Now you can

- 用同一个本地 UI 让真实 LLM 读取 profile 和 JD，并进行多轮追问
- 运行两类 Job Hunting baseline，观察裸模型的能力和不确定性
- 从 Inspector 和 trace 准确指出哪个 Python component 被调用，结果或错误在哪里

## Still cannot

- 不能保证 fit/gap 字段始终存在，类型也不一定正确
- browser transcript 不是经过验证、可以持续使用的 task state
- 不能证明事实性说法有 source 支持
- 不能用代码阻止不安全的外部操作

Lab 2 会保留相同材料和 fit/gap case，只新增 structured output 和 state。到时你应该能直接比较，它解决了这里的哪两个 limitation。

## 给 coding agent 的边界

给 Codex / Claude Code 时，不要只写“帮我做 Lab 1”。把范围和验收说清楚：

- 目标：完成 Lab 1 API baseline
- 修改范围：`model_client.py` 和 `baseline_tasks.json`
- 不改：tests、fixtures、golden outputs、`labs/shared/`
- 不新增：tools、state、RAG、multi-agent、新 provider、新产品 scope
- 执行顺序：先 unit tests，再 Gemini baseline
- 失败排查：先检查是否越过修改范围、是否漏了 API key、是否误解了 TODO；确认题目或测试本身有问题后再指出来
- 输出要求：总结 diff，并解释 harness logs 和 baseline observations 的含义

课堂上可能会问：为什么需要这样约束 coding agent。

## 抽查会看什么

准备好展示：

- `uv run python -m labs.shared.check_env`
- `uv run pytest labs/lab_01/tests`
- Gemini baseline 生成 `baseline_observations.json`
- UI中运行两个 capability examples
- Inspector显示 `ModelClient.complete()`和真实 status
- 解释 1 条 `student_note`
- 解释 model call 和 harness 的区别

## 重置 artifacts

reset 只清理本 lab artifacts：

```bash
uv run python scripts/reset_lab.py lab_01
```

如果 Codex / Claude Code 改动超出范围，用 git 或重新下载课程代码模板仓库恢复。

## 常见问题

- `uv: command not found`：先安装 `uv`，重新打开 terminal。
- Python 版本不对：用 Python 3.11+。
- `GOOGLE_API_KEY` missing：去 Google AI Studio 创建 key，然后点击网页右上角的 `API key missing` 填入。
- Gemini quota / network / auth error：保留错误信息，先检查 key、quota、网络和模型名；API key 不得出现在错误截图或日志片段里。
- tests fail after Codex edits：检查是否改了 tests/shared/fixtures；必要时恢复模板。

## 可选

- 给 observations 加更细的 risk category
- 写 1 段短评：为什么 baseline 不等于可靠 agent
