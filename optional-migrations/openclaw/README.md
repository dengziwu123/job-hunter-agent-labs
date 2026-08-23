# OpenClaw 课后迁移包

状态：Lab 7 Practical track 迁移说明

最后更新：2026-07-11

这几份文件给做完 Lab 6 的学生用。它不是必做主线，也不是 Lab 6/7 的评分前提。

课程里的必做主线仍然是 thin multi-agent harness：先在可控模板里练 role、contract、tool boundary、guardrail、budget 和 trace。做完后，如果想把 Job Hunter Agent 带回去长期用，可以按这份说明迁到 OpenClaw 或类似成熟 harness。

## 什么时候用

适合在这些时候用：

- Lab 6 已经能跑出 `artifacts/lab_06/multi_agent_trace.jsonl`
- 学生想把个人 Job Hunter Agent 放到成熟 harness 里继续维护
- 学生愿意让 Codex / Claude Code 读取当前 OpenClaw 文档或 examples，再生成迁移版本

下面这些情况先别用：

- Lab 6 还没过 tests
- guardrail、contract、budget 还没写清楚
- 只是为了通过课堂抽查
- 想跳过 Lab 1-6 直接用 OpenClaw

## macOS 与 Windows

OpenClaw 迁移路线支持 macOS 和 Windows，但 `openclaw` CLI 必须能从启动课程 UI 的同一个终端环境中找到：

- macOS：按 OpenClaw 官方安装说明配置 CLI / Gateway。
- Windows PowerShell：使用官方 Windows Hub 或 PowerShell CLI 安装路径。
- Windows WSL2：如果课程 UI 也在 WSL2 中运行，可以使用 WSL2 Gateway；如果课程 UI 在原生 PowerShell 中运行，WSL 内的 CLI 不会自动出现在 Windows `PATH` 中。

先在将要运行 `uv run uvicorn ...` 的同一个终端中执行：

```console
openclaw --version
openclaw agent --help
```

Windows 的当前官方选择和安装命令以 [OpenClaw Windows 文档](https://docs.openclaw.ai/windows) 为准。

## 迁移材料

先从课程发布目录取`job-hunting-product-spec.html`，把它作为文件交给 coding agent，或临时复制到 workspace 根目录。课程级产品规格不打进任何一个 Lab zip。

按顺序读：

1. `mapping.md`
2. `adapter-contract.md`
3. `MIGRATION_PROMPT.md`
4. `acceptance-checklist.md`

迁移版建议放到新目录，不要覆盖课程 lab：

```text
openclaw_job_hunter/
```

## 课程定位

Lab 1-5 讲 agentic system 的底层边界。Lab 6 在 thin harness 里做一次 multi-agent。OpenClaw 迁移包用于课后产品化：把同样的角色、contract、guardrail、budget 和 trace 迁到成熟 harness，减少长期维护成本。

Lab 7 demo 可以展示两种版本：

- Core track：thin harness demo
- Practical track：OpenClaw migration demo

两条路径任选其一完成 Lab 7。如果选择 OpenClaw，仍然要满足 Lab 7 的安全和 artifact 要求，并实现 `adapter-contract.md`，这样本地 UI 才能调用成熟 Harness 版本。

OpenClaw 当前是 CLI / Gateway runtime，不是本课程要 `pip install` 的 Python package。先按官方文档确认 `openclaw agent ... --json` 能在本机运行，再生成 Python process adapter并执行 smoke/acceptance cases。没有 CLI、真实 adapter或由测试生成的 `acceptance.json` 时，UI 会诚实显示 OpenClaw unavailable。
