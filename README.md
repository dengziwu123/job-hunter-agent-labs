# Job Hunter Agent Lab 05

> 如果你在升级旧版 Lab 5，请先在压缩包预览中阅读本 README，并在完整解压前执行第 1 节的备份步骤。首次从 Lab 4 进入 Lab 5 时，仍把它接到同一个 workspace；不要新建第二个 workspace。

## 1. 确认合并位置

Lab 05 会在原来的 workspace 里保留 `labs/lab_04/`，加入 `labs/lab_05/`，并更新顶层 `README.md`。之前 Lab 的代码、讲义和网页都会保留。

首次安装时，如果你是在 workspace 中看到这个 README，说明当前包已经放对位置。旧版 Lab 5 升级则必须先从 zip 预览本 README，并按下面顺序备份后再完整解压。

```console
uv run python scripts/check_lab_dependencies.py
```

检查结果应为 `Lab 05 dependency check passed.`。如果提示缺少 `labs/lab_05/stage.json` 或该 Lab 的 `src/` 安装标记，说明当前 zip 被解压到了别的目录，或当前 Lab 只有 runtime patch 放入的 metadata/adapter、并未完整安装；把当前包的内容合并到旧 workspace 的根目录，不要把两个 workspace 套在一起。

### 从旧版 Lab 5 安全升级：先备份，再完整解压

如果旧 workspace 已经有 `labs/lab_05/`，不要先完整解压当前 zip。旧版让学生编辑过 `evals/tasks.jsonl`、`evals/graders.py`、`reports/failure_analysis_template.md` 和 `labs/lab_05/src/known_failure.py`；迁移也会保留旧 `labs/lab_05/src/live_agent.py`。直接 overlay 会在备份前覆盖答案。请先在压缩包预览中阅读本 README，然后只取出不冲突的迁移 helper，运行备份，再执行下面的完整解压。

macOS Terminal（在旧 workspace 的上一级目录）：

```bash
unzip -o job-hunter-agent-labs-lab-05.zip labs/lab_05/archive_legacy_files.py -d job-hunter-agent-labs
cd job-hunter-agent-labs
uv run python labs/lab_05/archive_legacy_files.py --root .
cd ..
```

Windows PowerShell（在旧 workspace 的上一级目录）：

```powershell
tar -xf .\job-hunter-agent-labs-lab-05.zip -C .\job-hunter-agent-labs labs/lab_05/archive_legacy_files.py
Push-Location .\job-hunter-agent-labs
uv run python .\labs\lab_05\archive_legacy_files.py --root .
Pop-Location
```

helper 会在任何完整 overlay 之前，把上述五类旧文件逐字节移到 `labs/lab_05/legacy-backup/pre-issue-41/` 并保留原相对路径。备份目录名包含连字符、位于 active package 外，不会被 Python import。完整解压随后把当前课程文件安装回 active 路径；旧 `live_agent.py` / `known_failure.py` 只留在 backup。全新安装会报告没有旧文件；当前版本重复运行也不会移动 active 文件。

如果你已经先做了完整 overlay，仍然运行 helper：它会归档残留的旧 `live_agent.py` / `known_failure.py`，并明确警告三个共享路径的学生答案可能已被覆盖。helper 无法从被覆盖的文件恢复旧内容；请从你自己的 backup 或旧 workspace 找回。

如果你手边还有 zip，可以在旧 workspace 的上一级目录合并它。

macOS Terminal：

```bash
unzip -o job-hunter-agent-labs-lab-05.zip -d job-hunter-agent-labs
```

Windows PowerShell：

```powershell
Expand-Archive -Path .\job-hunter-agent-labs-lab-05.zip -DestinationPath .\job-hunter-agent-labs -Force
```

合并完成后，回到 workspace 再运行一次上面的检查。

## 2. 继续使用同一个网页

如果网页没有运行，执行：

```console
uv run uvicorn labs.shared.web.app:app --reload
```

## 3. 开始当前 Lab

[打开 Lab 05 讲义](instructions/lab-05-eval-debug.md)

课程总览和产品规格与七个 zip 放在同一个发布目录中。它们是课程级参考资料，不是开始当前 Lab 前的必读步骤。

本包不包含讲师标准答案。
