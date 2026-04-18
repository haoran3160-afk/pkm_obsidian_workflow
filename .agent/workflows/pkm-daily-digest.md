---
description: PKM Daily Digest - local-parity AI & Growth curation workflow
---

# PKM: 每日 AI & Growth Digest

当用户说“每日抓取”“今日 AI 日报”“更新知识库”时，严格按这套工作流执行。

目标不是“把抓到的东西全部总结一遍”，而是输出一份和本地工作流尽量一致的日报，栏目固定为：

- `Top 1`
- `Top 2`
- `Top 3`
- `创投洞见`
- `洞见`
- `今日视频`
- `洞见`

## Step 1：先生成 Raw 证据层

```powershell
cd D:\Agent_programs\obsidian_workflow_open
python main.py --raw-only
```

预期输出：

- `00-Inbox/Raw-Feeds/Raw-Daily-Feeds-YYYY-MM-DD.md`

## Step 2：选题前必须读取状态文件

先读：

- `used_articles.json`
- `source_rotation.json`

规则：

- 已在 `used_articles.json` 中出现过的 URL 不得再次入选。
- 优先选择最近 4 天未出现过的来源。
- 如果本周还没有使用 `3Blue1Brown`，则 `今日视频` 优先强制使用它。

## Step 3：严格按栏目配额选题

### 3a. Top 1-3

- 固定 3 条。
- 必须来自 AI 主新闻池。
- 默认不放论文和视频。
- 优先高信息密度、高方法细节、高实操价值条目。

### 3b. 创投洞见

- 1 条。
- 优先 `Sequoia Capital`。
- 如果当天没有足够信号，可由 `The Guardian Science` 这类宏观基础设施 / 商业格局文章补位。
- 使用深度模板。

### 3c. 洞见

- 1 条。
- 优先 `Dan Koe Blog`。
- 如果当天研究型 insight 更强，可由 `arXiv` 或分析型来源补位。
- 使用 brief 模板。

### 3d. 今日视频

- 1 条。
- 如果本周还没有出现 `3Blue1Brown`，优先强制使用。
- 否则按来源轮换。

### 3e. 洞见（AI 公司 / Solopreneur / 工具化）

- 1 条。
- 优先 `Google AI Blog` / `Lilian Weng` / `Hugging Face Blog` / `LangChain Blog`。
- 使用 brief 模板。

## Step 4：输出模板必须与本地版对齐

文件路径：

- `30-Daily/AI-News/AI-Daily-YYYY-MM-DD.md`

Frontmatter：

```yaml
---
title: "AI & Growth Digest - YYYY-MM-DD"
date: YYYY-MM-DD
tags:
  - daily-digest
  - AI-news
  - AI-solopreneur
type: "digest"
status: "inbox"
aliases: ["Daily Digest YYYY-MM-DD"]
---
```

### 深度模板

```markdown
## 🔥 Top 1 - {中文标题}

**来源**：{来源}
**原文**：[原文标题]({URL})
**核心概念**：#concept/A #concept/B

### 深度 Takeaways

**核心发现**：{1-2 句}

**关键细节**：
- {要点 1}
- {要点 2}
- {要点 3}

**行动启示**：{1 句}
```

### 简报模板

```markdown
## 🌱 洞见 - {中文标题}

**来源**：{来源}
**原文**：[原文标题]({URL})
**核心概念**：#concept/A #concept/B
**一句话**：{一句话总结}
**3 个要点**：
- {要点 1}
- {要点 2}
- {要点 3}
**行动启示**：{1 句}
```

### 视频模板

```markdown
## 📺 今日视频 - {中文标题}

**频道**：{频道}
**链接**：[原视频标题]({URL})
**时长**：{时长或未披露}
**核心概念**：#concept/A #concept/B
**核心结论**：{1-2 句}
**关键方法论**：
- {方法点 1}
- {方法点 2}
**行动启示**：{1 句}
```

## Step 5：成功写入后更新状态

- 把入选 URL 写回 `used_articles.json`
- 把入选来源写回 `source_rotation.json`
- 如果视频使用了 `3Blue1Brown`，更新 `weekly_summary.3blue1brown_used_this_week = true`

## Step 6：对用户汇报

只需要报告：

- 日报路径
- 实际入选栏目数
- 去重是否影响了最终选题
- 是否触发了 `3Blue1Brown` 周保底
- 是否使用了模型写作层，若没有，原因是什么
