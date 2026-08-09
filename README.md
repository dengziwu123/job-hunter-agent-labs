# Job Hunter Agent Lab 03

> 你已经打开 Lab 03 的 README。接下来把它接到完成 Lab 02 时使用的同一个 workspace 里。不要为每个 Lab 新建文件夹。

## 1. 确认合并位置

Lab 03 会在原来的 workspace 里保留 `labs/lab_02/`，加入 `labs/lab_03/`，并更新顶层 `README.md`。之前 Lab 的代码、讲义和网页都会保留。

如果你是在旧 workspace 中看到这个 README，说明当前包已经放对位置。在 workspace 根目录运行下面的检查：

```console
uv run python scripts/check_lab_dependencies.py
```

检查结果应为 `Lab 03 dependency check passed.`。如果提示缺少 `labs/lab_03/stage.json` 或该 Lab 的 `src/` 安装标记，说明当前 zip 被解压到了别的目录，或当前 Lab 只有 runtime patch 放入的 metadata/adapter、并未完整安装；把当前包的内容合并到旧 workspace 的根目录，不要把两个 workspace 套在一起。

如果你手边还有 zip，可以在旧 workspace 的上一级目录合并它。

macOS Terminal：

```bash
unzip -o job-hunter-agent-labs-lab-03.zip -d job-hunter-agent-labs
```

Windows PowerShell：

```powershell
Expand-Archive -Path .\job-hunter-agent-labs-lab-03.zip -DestinationPath .\job-hunter-agent-labs -Force
```

合并完成后，回到 workspace 再运行一次上面的检查。

## 2. 继续使用同一个网页

如果网页没有运行，执行：

```console
uv run uvicorn labs.shared.web.app:app --reload
```

## 3. 开始当前 Lab

[打开 Lab 03 讲义](instructions/lab-03-tools-actions.md)

课程总览和产品规格与七个 zip 放在同一个发布目录中。它们是课程级参考资料，不是开始当前 Lab 前的必读步骤。

本包不包含讲师标准答案。
