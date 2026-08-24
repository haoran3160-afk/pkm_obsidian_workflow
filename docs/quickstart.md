# Quickstart

当前实现是 `shadow-only / production blocked`。本指南只用于建立开发环境和确认安全基线，不会写入 Obsidian。

开始前先读：

- [Agentic Development Context](agentic/README.md)
- [Lightweight Specification](agentic/spec.md)
- [Implementation Tasks](agentic/tasks.md)

## 1. 环境

- Windows
- Python 3.10+
- 仓库：`D:/Agent_programs/obsidian_workflow_open`

按仓库现有环境安装开发依赖；不要安装或启动旧 React/FastAPI UI，不要运行根目录 `main.py`。

## 2. 确认当前阻断

```powershell
cd D:\Agent_programs\obsidian_workflow_open
.\.venv\Scripts\python.exe scripts\run_workflow.py --doctor --json
.\.venv\Scripts\python.exe scripts\run_workflow.py --dry-run --workflow ai --json
```

在 M1 实现完成前，两条命令预期返回退出码 `3`，并明确包含 `BLOCKED`、`vault_write=false` 或等价信息。非零是当前正确行为。

doctor 当前仍显示 native broker、AppContainer/WFP 等 v7.3 遗留 blocker。这反映的是现有代码尚未迁移，不表示轻量首发仍需完成这些重型组件；目标资格门以 [spec.md](agentic/spec.md) 为准。

## 3. 运行开发验证

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src/pkm_workflow
```

全历史测试仍包含要求旧覆盖写/旧路径的 retired expectations。开发 Agent 应完成 [tasks.md](agentic/tasks.md) 中的一个任务及其目标测试，不得通过重新启用旧行为来追求全历史绿色。

## 4. 开始开发

[T1.1–T1.3a](agentic/tasks.md) 已完成；T1.3b 已获准开发并由 [Current Development Goal](agentic/goal.md) 标记为唯一 Active Goal。它的实现与离线合约测试可以继续，但真实 Codex 调用、生产资格、Task 和 Vault 发布仍保持 `BLOCKED_PREQUALIFICATION`；只有固定原生 CLI、专用套餐登录、Windows 隔离与完整 shadow 证据通过并经用户批准后，才可解除该阻断。

## 5. 生产使用

当前没有受支持的生产命令，也没有应当启用的新 Task。AI Daily 必须经过真实端到端 shadow、7 天内容验收、create-new publisher、Task/Obsidian 资格和用户明确批准后才可启用。
