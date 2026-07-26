# Job Hunter Agent Lab 02

> 这是 Lab 02 的唯一入口。把本包合并到完成 Lab 01 时使用的 workspace，然后进入当前讲义。

## 1. 合并当前 Lab

```bash
cd job-hunter-agent-labs
unzip -o ../job-hunter-agent-labs-lab-02.zip
python scripts/check_lab_dependencies.py
```

不要新建第二个 workspace。如果检查失败，通常是解压目录里缺少前面的 Lab。

## 2. 继续使用同一个网页

如果网页没有运行，执行：

```bash
uv run uvicorn labs.shared.web.app:app --reload
```

## 3. 开始当前 Lab

[打开 Lab 02 讲义](instructions/lab-02-structured-state.md)

课程总览和产品规格与七个 zip 放在同一个发布目录中。它们是课程级参考资料，不是开始当前 Lab 前的必读步骤。

本包不包含讲师标准答案。
