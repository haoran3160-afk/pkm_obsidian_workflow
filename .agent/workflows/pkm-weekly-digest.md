---
description: PKM Weekly Digest — 科研论文 + AI 周报 + 3Blue1Brown（每周日执行）
---

# PKM: 每周智能摘要 (Weekly Digest)

当用户说「周报」、「本周汇总」、「weekly digest」时，或每周日自动触发时执行。

> [!IMPORTANT]
> **产出暂存在 `30-Daily/Weekly/` 目录，不含 `[[wikilinks]]`。**
> 用户审阅后运行 `/pkm-commit-daily` 正式收录进 Vault。

---

## Step 1: 获取本周 Raw Feeds

// turbo
```powershell
cd d:\personal\obsidian_workflow\pkm_workflow
D:\anacoda\python.exe daily_fetch.py --raw-only
```

---

## Step 2: 收集本周日报

读取 `D:\personal\Obsidian\30-Daily\AI-News\` 目录下本周（周一至周六）的所有 `AI-Daily-*.md` 文件，提取各日的精选内容。

同时读取 `D:\personal\Obsidian\00-Inbox\Raw-Daily-Feeds.md` 中的 arXiv 论文数据。

---

## Step 3: 生成 3 个板块

### 3a. 📄 科研论文速递（2-3 篇）

从本周 arXiv cs.AI / cs.LG 和 Papers With Code 数据中精选 2-3 篇**高引用潜力**论文。

**选题标准**：
- 聚焦数据科学 / AI 核心方向
- 含实验结果或开源代码的优先
- 避免纯理论无实验的论文

**每篇论文输出模板**：
```markdown
### 📄 {N}. {论文标题}

**作者**：{主要作者}
**链接**：[arXiv]({URL})
**代码**：[GitHub]({代码URL})（如有）

**核心贡献**：{2-3 句描述这篇论文解决了什么问题、提出了什么方法}

**方法/实验要点**：
- {技术要点 1}
- {技术要点 2}
- {关键实验结果}

**实用价值**：{对你的项目/学习有什么参考意义}
```

### 3b. 📊 AI 一周回顾

汇总本周 7 天日报中的所有 AI 资讯条目，生成结构化周报：

```markdown
## 📊 AI 一周回顾

### 🔮 一周核心趋势
{用 2-3 句话概括本周 AI 领域的整体动向}

### 🎯 3 个关键发现
1. **{发现 1 标题}**：{1-2 句解释}
2. **{发现 2 标题}**：{1-2 句解释}
3. **{发现 3 标题}**：{1-2 句解释}

### 💭 周思考
{基于本周信息，对个人/行业的思考，3-5 句}
```

### 3c. 🧮 3Blue1Brown 数学建模视频（1 条，**每周必须出现**）

> [!IMPORTANT]
> 此板块**强制输出，不可省略**。3Blue1Brown 是 demand.md 中明确要求的每周信息源。

**执行前检查 `source_rotation.json`**：
- 若 `weekly_summary.3blue1brown_used_this_week` 为 `true`（本周日报中已出现），直接使用 Raw Feeds 中的 3Blue1Brown 最新视频数据。
- 若为 `false` 或字段不存在，则从 Raw Feeds 的 YouTube 最新视频区找 3Blue1Brown 条目；若 Raw Feeds 中无数据，执行以下补抓：

```powershell
# 补抓 3Blue1Brown 最新视频（Raw Feeds 无数据时使用）
$rss = [xml](Invoke-WebRequest "https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw").Content
$latest = $rss.feed.entry | Select-Object -First 1
Write-Host "Title: $($latest.title)"
Write-Host "URL:   $($latest.link.href)"
```

从 3Blue1Brown YouTube 频道选取**最新 1 条**视频：

```markdown
## 🧮 本周数学视频 — {中文标题}

**频道**：3Blue1Brown
**链接**：[{视频标题}]({URL})
**时长**：{XX} 分钟
**核心概念**：{这个视频讲了什么数学/建模概念}
**关键方法论**：
- {要点 1}
- {要点 2}
**与数据科学的关联**：{这个概念如何应用于数据分析/建模}
```

输出完毕后，将 `source_rotation.json` 的 `weekly_summary.3blue1brown_used_this_week` 设为 `true`。

---

## Step 4: 输出

写入 `D:\personal\Obsidian\30-Daily\Weekly\AI-Weekly-{YYYY-MM-DD}.md`
（**文件内不含任何 `[[wikilinks]]`，所有链接使用 `[标题](URL)` 格式**）：

```markdown
---
title: "AI & Paper Weekly - YYYY-MM-DD"
date: YYYY-MM-DD
tags: 
  - weekly-digest
  - AI-news
  - papers
type: "digest"
status: "inbox"
aliases: ["Weekly Digest YYYY-MM-DD"]
---
```

---

## Step 5: 知识库健康检查（自动联动）

// turbo
```powershell
cd d:\personal\obsidian_workflow\pkm_workflow
D:\anacoda\python.exe knowledge_health_check.py
```

---

## Step 5: 清理并报告

// turbo
```powershell
Remove-Item "D:\personal\Obsidian\00-Inbox\Raw-Daily-Feeds.md"
```

告知用户：
- 周报已暂存至 `30-Daily/Weekly/AI-Weekly-{date}.md`
- 包含：科研论文 2-3 篇 + AI 周报 + 3Blue1Brown 视频
- 🩺 知识库健康检查已完成（报告位于 `40-MOC/lint-report-{date}.md`）
- ⚠️ 不含 wikilinks，学习后运行 `/pkm-commit-daily` 收录
