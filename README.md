# Job Hunter Agent Lab 01

> 你已经将 Lab 1 解压到这个文件夹。完成下面的初始化，然后进入 Lab 1 讲义。

## 1. 初始化 workspace

```bash
python scripts/check_lab_dependencies.py
uv sync
cp .env.template .env
```

## 2. 启动课程网页

```bash
uv run uvicorn labs.shared.web.app:app --reload
```

打开 `http://127.0.0.1:8000`，点击右上角的 `API key missing`，填入 Gemini API key。网页会把它保存到当前 workspace 的 `.env`；不要提交或分享这个文件。

Lab 1 到 Lab 7 都使用这个 workspace 和网页。

## 3. 开始当前 Lab

[打开 Lab 01 讲义](instructions/lab-01-api-baseline.md)

课程总览和产品规格与七个 zip 放在同一个发布目录中。它们是课程级参考资料，不是开始当前 Lab 前的必读步骤。

本包不包含讲师标准答案。
