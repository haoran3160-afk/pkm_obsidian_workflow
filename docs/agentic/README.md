# Agentic Development Context

状态：`READY_FOR_DEVELOPMENT`

生产状态：`SHADOW_ONLY / BLOCKED`

v7.4 方向生效日期：2026-08-24

这里是 PKM Workflow 的唯一规范文档集。目标是让开发 Agent 用最少上下文完成正确实现，不再从 README、历史工作流和验证产物中拼接相互矛盾的要求。

## 文档地图

| 文档 | 回答的问题 | 更新时机 |
|---|---|---|
| [product.md](product.md) | 为什么做、为谁做、什么结果才有价值 | 用户目标或成功指标改变 |
| [spec.md](spec.md) | 系统必须做什么、禁止做什么 | 可观察行为或边界改变 |
| [architecture.md](architecture.md) | 用什么最小结构实现规格 | 模块、信任边界或技术选择改变 |
| [development.md](development.md) | Agent 如何开发、验证和交付 | 工程流程或质量门改变 |
| [tasks.md](tasks.md) | 下一步具体做什么 | 每个任务开始与完成时 |
| [goal.md](goal.md) | 当前会话可以直接执行什么 | `NEXT` 任务或边界改变 |
| [decisions.md](decisions.md) | 为什么选择或放弃某条路线 | 出现跨模块、依赖或威胁模型决策 |

根目录 [AGENTS.md](../../AGENTS.md) 是 Agent 入口，以上文档是其展开内容。

## 优先级

1. 用户当轮明确约束。
2. `AGENTS.md` 的永久数据边界。
3. `spec.md` 的行为契约。
4. `architecture.md` 的实现约束。
5. `tasks.md` 的执行顺序。
6. `goal.md` 的当前执行范围。
7. 其他文档和历史实现。

发现冲突时停止扩散实现，先修正文档事实源。不得为了兼容旧测试而违反上层约束。

## 当前与目标

当前 wheel 和 CLI 会正确返回 `BLOCKED`，不会写 Vault；这是安全基线，不是完整产品。目标实现是：

```text
Windows Task Scheduler
        ↓
单一 Python Orchestrator + 全局锁
        ↓
固定来源抓取 → 证据标准化 → Coverage
        ↓
确定性候选筛选 → DeepSeek Generator(JSON)
        ↓
确定性事实校验 → fresh DeepSeek Reviewer（逐 claim 结构化决策）
        ↓
确定性 Markdown Renderer
        ↓
runtime backing → os.link(create-new) → Obsidian
        ↓
create-new Status + Home
```

## 历史资料

以下内容保留用于审计和取证，但不是当前开发指令：

- 根目录 `main.py`、`writer.py`、`ui_server.py` 和旧 UI/API 文档。
- `.agent/workflows` 中警告块之后的历史执行步骤。
- `native/` 的 v7.3 stage0/broker 实验。
- 私有仓库的 `PKM-Workflow-优化方案-v7.3.md` 与旧验证产物。

不得删除这些文件，也不得从中恢复旧的 Vault 覆盖写、全库扫描、常驻 daemon 或重型生产资格门。

## 开发就绪结论

轻量化方向已完成第一性原理审查：它覆盖最可能造成实际损失的远程恶意内容、证据错误、路径越界、覆盖写、重复任务和失败误报，同时不把可信单用户本地工作流建设成多租户安全平台。当前 DeepSeek provider 已完成 transport/schema smoke，但 versioned pipeline adapter、真实内容 shadow、Obsidian 展示和 Windows 调度仍未完成，不被文档假定为已完成。
