# Obsidian Workflow Open 改造 Walkthrough

## 1. 本次改造目标

本轮改造聚焦三个问题：

1. Top 区被论文占满，资讯结构失衡。
2. 数据源健康状态不透明，排障成本高。
3. 日报模板虽然稳定，但缺少可操作的质量约束与验收标准。

## 2. 核心改造内容

### 2.1 日报生成（`formatter.py`）

- Top 区改为“多样性约束选择”：
  - `min_top_nonpaper`：Top 区最少非论文条数
  - `min_top_content_types`：Top 区最少内容类型覆盖数
  - `max_paper_in_top`：Top 区最多论文条数
- 新增 Top 多样性快照与未达标告警。
- 统一日报主标题为 `AI 每日简报`，关键章节改为中文表达（如“深度要点”）。
- 低信号论文不直接进入复现动作，改为进入 `paper-radar.md` 跟踪。

### 2.2 抓取健康诊断（`fetcher.py` + `main.py`）

- RSS 抓取支持 fallback 元信息回传（`return_meta=True`），在主流程记录：
  - 抓取模式（direct / fallback-url / fallback-http-*）
  - 过滤、低分淘汰、裁剪、全文增强计数
- Source health 明细长度提升，便于定位异常 feed。

### 2.3 配置治理（`config_schema.py` + `pkm_config.json`）

新增配置项：

- `daily_digest_min_top_nonpaper`
- `daily_digest_min_top_content_types`
- `daily_digest_max_paper_in_top`

并修正了几个高频来源配置（频道 ID / RSS URL），减少抓取空跑：

- DeepLearning.AI YouTube channel_id
- AI Engineer YouTube channel_id
- Dan Koe YouTube channel_id
- LangChain Blog RSS URL

### 2.4 文档与样例

- 更新 `README.md` 配置表，补充 Top 多样性参数说明。
- 重写 `docs/sample_outputs/ai-daily-brief-sample.md`，与当前模板一致。

## 3. 验证步骤（可直接复现）

### 3.1 单元测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

预期：全量通过。

### 3.2 流程级 dry-run

```powershell
.\.venv\Scripts\python.exe main.py --test --dry-run
```

预期：

- 命令成功退出。
- 输出 Run Summary 表格（RSS/YouTube 抓取状态）。
- 不写入 Obsidian（`Files written: 0`）。

## 4. 产出验收标准（用户视角）

打开当日 `AI-Daily-YYYY-MM-DD.md` 后，重点检查：

1. Top 区是否至少包含新闻/工程/推文中的两类，不再是“纯论文墙”。
2. 每条 Top 是否包含：核心发现、关键细节、行动启示。
3. `今日快照` 是否出现 Top 多样性状态（达标/未达标）。
4. 低信息密度论文是否被降级到雷达跟踪，而不是直接给出复现任务。

## 5. 建议的默认参数

```json
{
  "daily_digest_top_picks": 6,
  "daily_digest_min_top_nonpaper": 2,
  "daily_digest_min_top_content_types": 2,
  "daily_digest_max_paper_in_top": 1
}
```

这组参数兼顾“前沿论文雷达”与“工程可执行信号”，适合日更节奏。
