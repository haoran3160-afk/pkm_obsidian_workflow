# AI Daily Workflow

基于 Codex、Python 与 Obsidian 的个人 AI 资讯工作流。

它把批准的信息源整理成可追溯的证据包，由 Codex 定时任务启动两个独立的 GPT-6 Luna 上下文：

    approved sources
      → RSS / GitHub / paper collection
      → free-body evidence and deduplication
      → Luna Generator
      → independent Luna Reviewer
      → deterministic Markdown renderer
      → create-new Obsidian note

日报回答“今天值得看什么”；周报回答“这一周发生了什么结构性变化”。Python 负责来源、证据、schema、审核绑定、渲染和写入，模型不决定最终路径、URL 或 Markdown。

## 运行入口

当前唯一运行入口是：

    .\.venv\Scripts\python.exe scripts\run_workflow.py --workflow ai --mode shadow --stage prepare
    .\.venv\Scripts\python.exe scripts\run_workflow.py --workflow ai --mode production --stage prepare
    .\.venv\Scripts\python.exe scripts\run_workflow.py --workflow ai --mode production --stage review --run-id RUN_ID
    .\.venv\Scripts\python.exe scripts\run_workflow.py --workflow ai --mode production --stage finalize --run-id RUN_ID --confirm-vault-write

生产任务由 Codex automation 在上海时间 08:00 编排。运行引擎固定使用：

    D:\Agent_programs\obsidian_workflow_mvp

已有笔记永不覆盖；失败、证据不足或审核不完整时不生成半成品。

## 内容节奏

| 日期 | 日报模块 |
|---|---|
| 周一 / 周四 | 学术研究 + AI 实践 |
| 周二 / 周五 | Builder + 创投产业 |
| 周三 / 周六 | 认知成长 + GitHub 项目 |
| 周日 | 全周跨领域综合周报 |

六个兴趣方向只用于来源治理和候选筛选，不强制周报逐栏填充。周报优先形成全周主判断，再选择趋势、早期信号、关键个案和下周观察。

## 可靠性边界

- Generator 与 Reviewer 使用独立 Luna 上下文；Reviewer 不能与 Generator 使用相同 session；
- 每个 claim 必须引用封存 evidence ID；
- Reviewer 决策、evidence ID、hash 和 reason 必须完整匹配；
- 只有通过审核的内容进入确定性渲染；
- 使用运行锁和 create-new，重复运行返回 ALREADY_EXISTS；
- free-body 不可验证、Coverage 不足、schema 错误或模型角色不可用时停止；
- 不使用 DeepSeek、OpenAI API、Codex CLI 或 provider fallback；
- 不修改 .obsidian，不扫描 Vault 其他内容，不安装推荐项目。

## 目录

    src/pkm_workflow/
      ai_daily_luna.py          三阶段编排、证据绑定与发布
      ai_daily_production.py   create-new 发布与 receipt
      module_collection.py      RSS、GitHub、论文候选
      daily_content.py          evidence packet 与审核要求
      daily_brief.py            日报 schema、校验、渲染
      weekly_collection.py      周报材料池
      weekly_brief.py           周报综合 schema、校验、渲染
      source_catalog.py         批准来源目录
      v75_collection.py         Candidate / Coverage / evidence 基础类型
    scripts/run_workflow.py     CLI 入口
    tests/                      当前运行链回归测试
    docs/ai-daily-luna.md       Codex automation 运行手册

旧的 Rust/native、UI/API、旧 Task installer、DeepSeek provider 和历史入口不属于当前运行链；它们不参与安装、调度或生产发布。

## 开发验证

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\ruff.exe check src tests
    .\.venv\Scripts\mypy.exe src
    git diff --check

## 隐私与许可

来源配置、用户上下文、运行时 receipt 与 Vault 内容不放入本仓库。仓库只保存可复用的 Python 引擎、schema、测试和运行手册。

MIT License
