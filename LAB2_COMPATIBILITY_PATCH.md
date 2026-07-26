# Lab 2 compatibility note for existing workspaces

这份说明适用于已经完成 Lab 2、再安装当前 v3 browser overlay 的学生。

## 不需要重做或手工修改 Lab 2

当前 v3 overlay 已经直接包含课程维护的：

```text
labs/lab_01/web_adapter.py
labs/lab_02/web_adapter.py
```

正常解压 v3 后，不需要让 coding agent 修改 Lab 2，也不需要再手工添加 helper 参数。

overlay 只覆盖课程提供的网页 adapter，不覆盖学生作答的文件：

```text
labs/lab_01/src/
labs/lab_02/src/
```

因此已有的模型调用实现、structured prompt、schema、state transition、fixtures、artifacts 和
API 配置都会保留。

## v3 自动补齐的兼容能力

更新后的 Lab 2 adapter：

- 接受新版 Lab 3/6 使用的 `user_request` 和 `raw_user_request` 参数；
- 遇到旧版 one-shot `run_from_objects` 时，不会传入它不支持的参数；
- 记录真实的 system prompt、当前 user request、provider input 和 raw model output；
- 让这些信息出现在 Diff 两侧的 **Prompt & model I/O** 和 Harness Inspector 中。

如果旧 Lab 2 本身没有把当前 user request 放进 model messages，I/O 面板会如实显示这个事实；
overlay 不会在模型外伪造或改写学生的 prompt。

## 安装

在已有 workspace 中执行：

```bash
unzip -o ../job-hunter-agent-browser-overlay-v3.zip -x README.md
uv run uvicorn labs.shared.web.app:app --reload
```

## 无 API 验证

```bash
python -m py_compile \
  labs/lab_01/web_adapter.py \
  labs/lab_02/web_adapter.py

python -c "import inspect; from labs.lab_02.web_adapter import build_structured_capability; p=inspect.signature(build_structured_capability).parameters; assert {'user_request', 'raw_user_request'} <= set(p); print('Lab 2 v3 adapter compatibility passed')"
```

然后在网页中运行一次 Lab 1 → Lab 2 Diff，分别展开两侧的
**Prompt & model I/O**。成功和失败的模型调用都应保留可见的输入信息；成功调用还会显示 raw
model output。
