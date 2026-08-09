# Lab 2 compatibility note for existing workspaces

这份说明适用于已经完成 Lab 2、再安装当前 v5 browser patch 的学生。

## 不需要重做或手工修改 Lab 2

当前 v5 patch 已经直接包含课程维护的：

```text
labs/lab_01/web_adapter.py
labs/lab_02/web_adapter.py
```

正常解压 v5 后，不需要让 coding agent 修改 Lab 2，也不需要再手工添加 helper 参数。

patch 只覆盖课程提供的网页 adapter，不覆盖学生作答的文件：

```text
labs/lab_01/src/
labs/lab_02/src/
```

因此已有的模型调用实现、structured prompt、schema、state transition、fixtures、artifacts 和
API 配置都会保留。

## patch 自动补齐的兼容能力

更新后的 Lab 2 adapter：

- 接受新版 Lab 3/6 使用的 `user_request` 和 `raw_user_request` 参数；
- 遇到旧版 one-shot `run_from_objects` 时，不会传入它不支持的参数；
- 记录真实的 system prompt、当前 user request、provider input 和 raw model output；
- 让这些信息出现在 Diff 两侧的 **Baseline pipeline / Why output changed** 和 Harness Inspector 中。

如果旧 Lab 2 本身没有把当前 user request 放进 model messages，patch 会在课程维护的
`ModelIoRecorder` I/O 边界补上 bounded request context，再交给真实 provider；这只改变
课程记录和发送给 provider 的 model input，不改写学生的 `src/`、TODO 或 Lab 2 的模型实现。
新版 Lab 2 入口则优先通过可选 `user_request` 参数传递，旧版 two-argument 入口走这个
course-owned I/O compatibility bridge。

## 安装

在已有 workspace 中执行。

macOS Terminal：

```bash
unzip -o ../job-hunter-agent-browser-patch-v5.zip -x README.md
uv run uvicorn labs.shared.web.app:app --reload
```

Windows PowerShell：

下面三条命令需要连续运行完成，不要在 README 写回前中断。

```powershell
$readme = [IO.File]::ReadAllBytes((Resolve-Path .\README.md))
Expand-Archive -Path ..\job-hunter-agent-browser-patch-v5.zip -DestinationPath . -Force
[IO.File]::WriteAllBytes((Join-Path (Get-Location) "README.md"), $readme)
uv run uvicorn labs.shared.web.app:app --reload
```

如果之后重新解压本地已下载的旧 Lab zip，请再次应用 v5。不要重新解压已经完成的
Lab 1/2 zip：它包含 starter `src/`，会覆盖你的作答；只想更新 runtime 就使用 browser patch。

## 无 API 验证

macOS Terminal：

```bash
uv run python -m py_compile \
  labs/lab_01/web_adapter.py \
  labs/lab_02/web_adapter.py

uv run python -c "import inspect; from labs.lab_02.web_adapter import build_structured_capability; p=inspect.signature(build_structured_capability).parameters; assert {'user_request', 'raw_user_request'} <= set(p); print('Lab 2 adapter compatibility passed')"
```

Windows PowerShell：

```powershell
uv run python -m py_compile labs/lab_01/web_adapter.py labs/lab_02/web_adapter.py
uv run python -c "import inspect; from labs.lab_02.web_adapter import build_structured_capability; p=inspect.signature(build_structured_capability).parameters; assert {'user_request', 'raw_user_request'} <= set(p); print('Lab 2 adapter compatibility passed')"
```

然后在网页中运行一次 Lab 1 → Lab 2 Diff，分别查看两侧的
**Baseline pipeline / Why output changed**。展开其中的 model step；成功和失败的模型调用都应
保留可见的输入信息，成功调用还会显示 raw model output。
