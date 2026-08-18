# PKM Workflow Agent Guide

本文件是本仓库开发 Agent 的唯一入口。当前目标不是恢复旧工作流，而是按轻量化规格实现一个适合单用户、可信 Windows 主机的本地资讯工作流。

## 必读顺序

1. `docs/agentic/README.md`：文档地图、优先级和当前阶段。
2. `docs/agentic/product.md`：用户问题、结果指标和非目标。
3. `docs/agentic/spec.md`：不可破坏约束与可验收行为。
4. `docs/agentic/architecture.md`：目标架构和信任边界。
5. `docs/agentic/development.md`：开发、测试和交付规则。
6. `docs/agentic/tasks.md`：当前任务顺序与完成定义。
7. `docs/agentic/goal.md`：当前唯一可执行开发 Goal。
8. `docs/agentic/decisions.md`：已做出的架构取舍。

发生冲突时，按上述顺序处理；行为约束以 `spec.md` 为准，实施细节以 `architecture.md` 为准。根目录旧 Python 入口、旧 UI 文档、`.agent/workflows` 中的历史正文和 `native/` 不是当前规格。

## 当前阶段

- 规格状态：`READY_FOR_DEVELOPMENT`。
- 运行状态：`SHADOW_ONLY / PRODUCTION_BLOCKED`。
- 当前受支持的命令只用于确认阻断状态：

  ```powershell
  .\.venv\Scripts\python.exe scripts\run_workflow.py --doctor --json
  .\.venv\Scripts\python.exe scripts\run_workflow.py --dry-run --workflow ai --json
  ```

- 当前 doctor 仍会列出 `NATIVE_BROKER_NOT_QUALIFIED`、`APPCONTAINER_WFP_NOT_QUALIFIED` 等 v7.3 遗留 blocker；它们证明旧引擎尚未迁移，不是轻量规格的新上线要求。T1.5 应把 gate 改为 `spec.md` 的真实资格项，不能通过伪造通过值消掉提示。

- 不得启用 Task、写 Vault、创建 Home/Status 或把现有 `BLOCKED` 改成成功，除非对应任务的验收标准已完成且用户明确批准该阶段。

## 永久边界

- 现有信息源与记录只增不删；不得自动禁用、降权、改分母或重分类来制造 Coverage。
- 不删除、移动或重命名两个仓库和 Vault 中的任何文件。
- 不扫描 Vault 其他内容；不得读取或写入 `10-Notes/*`、`20-Sources/*`、`.obsidian/*`。
- Vault 只允许 create-new 写入 `01-Raw/daily-feeds`、`30-Daily/AI-News`、`30-Daily/Weekly`、`30-Daily/IELTS-Preview`、`30-Daily/PKM-Workflow-Status` 和 create-once Workflow Home/Bases。
- 目标存在时不得覆盖；创建带 `run_id` 的冲突副本并报告 `CONFLICT`。
- 不购买 API，不启用 DeepSeek 生产回退；Codex 只使用用户已有套餐。
- 不唤醒电脑；恢复后由 `--due-only` 补跑。

## 开发纪律

- 一次只执行 `goal.md` 中一个 Goal；它必须对应 `tasks.md` 中的当前 `NEXT` 任务。开始前复现失败或写出失败测试。
- 每个任务保持小改动，通常不超过 5 个代码/测试文件。
- 生产入口和测试必须调用同一实现；禁止 test-only 分支、硬编码最终结果或修改期望来迁就代码。
- 优先复用 `src/pkm_workflow` 现有模块；旧根模块只作迁移参考，不重新激活。
- 未经 `decisions.md` 新记录，不新增 Rust、常驻服务、数据库、消息队列、WFP/AppContainer、MAC journal 或新的依赖。
- 任何模型输出都按不可信数据处理；只有确定性校验器和 renderer 能形成最终 Markdown。
- 任何不确定的安全或数据边界均 fail-closed，并把真实非零退出码返回给调用者。

## 完成一个任务前

- 运行任务列出的最小测试、Ruff 和 Mypy；需要时再跑全量回归。
- 核验没有 Vault 写入，没有 tracked 文件删除/重命名，没有旧入口重新可达。
- 更新 `tasks.md` 的证据链接；若行为或架构改变，先更新对应 canonical 文档。
- 生产激活永远需要用户明确批准，程序不得自行批准。
