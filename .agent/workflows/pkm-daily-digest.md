---
description: PKM Daily Digest — Agentic Curation (News + IELTS Listening + Reading)
---

# PKM: 每日智能资讯策展 (Main Agent + SubAgent 架构)

当用户说「每日拉取」、「今日资讯」、「更新知识库」时，按以下 Agentic Workflow 严格执行。

> [!IMPORTANT]
> **所有 Daily 产出均暂存在 `30-Daily/` 目录，不含 `[[wikilinks]]`，不出现在 Graph View 中。**
> 用户学习并选择后，再运行 `/pkm-commit-daily` 正式收录进 Vault。

---

## Step 1: 获取 Raw Feeds

// turbo
```powershell
cd d:\personal\obsidian_workflow\pkm_workflow
D:\anacoda\python.exe daily_fetch.py --raw-only
```

执行成功后，原始数据写入 `D:\personal\Obsidian\00-Inbox\Raw-Daily-Feeds.md`（含 RSS + arXiv 论文 + YouTube 视频）。

---

## Step 2: [News SubAgent] 每日 7 项精选

### ⚠️ Step 2a. 去重预检（强制执行）

**在选择任何文章之前，必须先读取历史记录文件**：
`d:\personal\obsidian_workflow\pkm_workflow\used_articles.json`

去重规则：
- 文件中 `articles` 数组记录了过去 7 天内所有已使用的文章 URL
- **严禁选择**任何已出现在该文件中的 URL（即使它是当天 Raw Feeds 中信息密度最高的文章）
- 如果某个板块的所有可用文章都已被使用过，**该板块留空**并在输出末尾注明「本板块今日无新内容」
- 对于 RSS 输出为空的源（如 Ben's Bites / TLDR AI 偶尔为空），可以从 HackerNews 中挑选符合该板块主题的文章作为替代，但同样不得与历史重复

### ⚠️ Step 2b. 来源轮换预检（强制执行）

**同时读取来源轮换追踪文件**：
`d:\personal\obsidian_workflow\pkm_workflow\source_rotation.json`

轮换规则（按优先级顺序执行）：

**Priority 1 — 3Blue1Brown 每周保底**
- 检查 `weekly_summary.3blue1brown_used_this_week` 字段
- 若为 `false`（本周尚未出现过），则**今日 📺 YouTube 板块强制选择 3Blue1Brown 的最新视频**，不可选其他 YouTube 频道
- 若为 `true`（本周已出现），正常轮换逻辑选 YouTube

**Priority 2 — 全周信息源轮换**
- 计算今日日期减去每个来源的 `last_used` 日期
- **优先选择距上次使用 ≥ 4 天的来源**（避免同一来源连续多天出现）
- 若某来源 ≥ 7 天未被使用，**视为高优先级**，当天必须选中
- 在同一板块内多个候选来源中，始终选 `last_used` 最早的那个

**轮换适用板块**：
| 板块 | 可选来源 | 轮换策略 |
|------|---------|---------|
| 🔥 AI 资讯 | HackerNews / The Batch / The Rundown AI | 3 条可来自不同源，优先末次使用最早的 |
| 🌱 成长洞见 | Dan Koe Blog | 固定单源，无需轮换 |
| 🤖 AI Solopreneur | Ben's Bites / TLDR AI | 两源交替，优先最久未用的 |
| 📺 YouTube | 9 个频道 | 见 Priority 1 规则，其余按 last_used 轮换 |

读取 `D:\personal\Obsidian\00-Inbox\Raw-Daily-Feeds.md`，按以下规则精选 **5 个板块共 7 项内容**：

### 精选范围

| # | 板块 | 数量 | 深度 | 来源 |
|---|------|:----:|------|------|
| 1 | 🔥 AI 资讯 | 3 条 | 精读 | HackerNews / The Batch / The Rundown AI |
| 2 | 💰 创投洞见 | 1 条 | 精读 | Sequoia Capital |
| 3 | 🌱 成长洞见 | 1 条 | 概述 | Dan Koe Blog |
| 4 | 📺 YouTube 推荐 | 1 条 | 精看 | 9 个已配置 YouTube 频道（Priority 1 规则优先） |
| 5 | 🤖 一人 AI 公司 | 1 条 | 概述 | Ben's Bites / TLDR AI（交替轮换） |

### 内容筛选规则

1. **剔除**：营销文、招聘广告、泛科学水文、纯事件播报（不带观点的）
2. AI 资讯聚焦 AI/LLM/大模型落地，高信息密度、与核心目标强相关
3. 对精读类文章：直接访问原文，提取深度 Takeaways
4. **强制要求**：所有超链接必须使用可点击的 Markdown 格式：`[链接文字](完整URL)`，严禁裸链接
5. **去重**：所有选中的文章 URL 不得出现在 `used_articles.json` 的历史记录中

### Takeaways 输出模板（严格统一，每次必须遵守）

#### 最佳实践要求
- 必须抽取 2-3 个关键的 **概念标签 (Concept Tags)**，格式为 `#concept/X`，以便未来建立双向链接图谱。
- 必须输出明确的 **行动启示 (Actionable Insight)**，将信息转化为具体步骤。

#### 精读类（AI 资讯 / 创投洞见）

```markdown
## 🔥 Top {N} — {中文标题}

**来源**：{来源名称} · {HN Xpts / Sequoia / etc.}
**原文**：[{英文原标题}]({URL})
**🔑 核心概念**：#concept/A #concept/B

### 深度 Takeaways

**核心发现**：{1-2 句核心结论}

**关键细节**：
- {要点 1}
- {要点 2}
- {要点 3}

**💡 行动启示**：{1句话描述如何将此文章的思路用于实际应用或思考模型中}
```

#### 概述类（成长洞见 / 一人 AI 公司）

```markdown
## 🤖 洞见 — {中文标题}

**来源**：{来源名称}
**原文**：[{标题}]({URL})
**🔑 核心概念**：#concept/A #concept/B
**一句话**：{核心观点，30 字以内}
**3 个要点**：
- {要点 1}
- {要点 2}
- {要点 3}
**💡 行动启示**：{1句话描述能马上落地的 To-Do 或产品点子}
```

#### YouTube 视频类

```markdown
## 📺 今日视频 — {中文标题}

**频道**：{频道名}
**链接**：[{视频标题}]({URL})
**时长**：{XX} 分钟
**🔑 核心概念**：#concept/A #concept/B
**核心结论**：{1-2 句}
**关键方法论**：
- {要点 1}
- {要点 2}
**💡 行动启示**：{视频带给你的 1个核心技术启发或学习方向}
```

### 输出文件

将精选内容写入：
`D:\personal\Obsidian\30-Daily\AI-News\AI-Daily-{YYYY-MM-DD}.md`

```markdown
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

### Step 2b. 更新去重历史（写入后必须执行）

将本次精选的**所有文章 URL**（含视频链接）追加到 `d:\personal\obsidian_workflow\pkm_workflow\used_articles.json` 的 `articles` 数组中，格式为：
```json
{"date": "YYYY-MM-DD", "url": "https://..."}
```

同时，**删除** `articles` 数组中 `date` 早于 7 天前的条目（自动过期清理）。

### Step 2c. 更新来源轮换记录（写入后必须执行）

更新 `d:\personal\obsidian_workflow\pkm_workflow\source_rotation.json` 中每个已选来源的 `last_used` 字段为今日日期：
```json
"Ben's Bites (AI Solopreneur)": {"last_used": "YYYY-MM-DD", "category": "AI Solopreneur"}
```

若本日 YouTube 板块选了 3Blue1Brown，将 `weekly_summary.3blue1brown_used_this_week` 设为 `true`。
若本周一（周一）新周开始，将 `weekly_summary.week_start` 更新为本周周一日期，并将 `3blue1brown_used_this_week` 重设为 `false`。

---

## Step 3: [IELTS SubAgent] 双材料精选（听力 + 阅读）

你需要作为资深英语教研专家，从**真实英文网站**（非 IELTS 聚合站）精选**两份**材料，主题必须契合雅思考试常见议题，且**两篇主题不得重复/相似**。

### IELTS 常见考试主题池（每次跨域从不同类选择，务必多样化）
- 🌍 Environment & Climate Change
- 🏥 Health & Medicine / Psychology
- 🏛️ History, Archaeology & Heritage
- 🐾 Wildlife, Ecology & Conservation
- 🏙️ Urbanisation & Social Policy
- 📚 Education & Child Development
- 💼 Business, Economy & Entrepreneurship
- 🔬 Science & Technology（含 AI & Society 方向）
- 🎨 Art, Culture & Media
- 🍽️ Food, Agriculture & Global Supply

> **选题规则**：每天听力和阅读**主题不同**，且连续两天不重复同一主题。AI 相关内容（如 AI 伦理、AI 与工作、AI 与教育）可以出现，但需以雅思考试角度视角呈现（词汇、观点、论证逻辑），而非技术层面，且不能连续两天都选 AI 主题。


### 3a. 精选听力材料 (Listening Practice)
从以下来源挑选 1 集/段（5-15 分钟的音频/视频）：
- **BBC Radio 4** — "In Our Time"、"The Life Scientific"、"More or Less"
- **TED Talks** — 非 AI 科技类，演讲速度中等，英语清晰
- **BBC Documentary** — Nature, History, Science 类节目片段

要求：访问实际页面，获取**直接可播放的 URL**，不能是列表页。

### 3b. 精选阅读材料 (Reading Practice)
从以下来源挑选 1 篇（600-900 词，学术类或深度报道）：
- **BBC Future / Science Focus** — 科学分析类长文
- **The Guardian / BBC Earth** — 环保、历史、社会深度报道
- **Scientific American** / **National Geographic** — 科学人文类

要求：避免 AI 相关主题，主题与当天听力材料不同。

### ⚠️ Step 3c. 强制 URL 验证（写入前必须执行）

**在将任何链接写入 Obsidian 文件之前，必须通过 `read_url_content` 工具验证每条链接可正常访问。**

验证规则：
- 若工具成功返回文章内容（有 Title、有正文 chunks）→ 链接有效，可以写入
- 若工具返回 404 / 403 / 连接失败 → **立即换一篇，重新验证，直到找到可访问的文章为止**
- 验证通过后，写入推荐理由末尾加标注：`（已验证可访问 ✅）`，且**文件内的所有 URL 必须使用 `[文章标题](完整URL)` 格式以保证可点击**
- 禁止写入任何未经验证的 URL

### 输出格式
写入 `D:\personal\Obsidian\30-Daily\IELTS-Preview\IELTS-Preview-{YYYY-MM-DD}.md`
（**文件内不含任何 `[[wikilinks]]`**）：

```markdown
---
title: "IELTS Daily Preview - YYYY-MM-DD"
date: YYYY-MM-DD
tags: ["Daily", "IELTS-Preview"]
type: daily-preview
status: pending-review
---

## 🎧 今日听力材料

**主题分类**：[从主题池选择]
**来源**：[BBC Radio 4 / TED Talks / 其他]
**时长**：约 XX 分钟
**直接链接**：[文章/节目标题](完整URL)
**推荐理由**：[说明：包含什么高频词汇、语速特点、对应雅思哪种题型]

---

## 📖 今日阅读材料

**主题分类**：[从主题池选择，与听力不同]
**来源**：[BBC Future / The Guardian / 其他]
**字数**：约 XXX 词
**直接链接**：[文章标题](完整URL)
**推荐理由**：[说明：词汇难度、段落结构、对应哪种 IELTS Reading 题型的训练]

---

## 📝 打卡模板（学习后填写）

### 听力打卡
- [ ] 完成收听
- [ ] 核心词汇 3-5 个：
- [ ] 主旨概括（1句话）：

### 阅读打卡
- [ ] 完成阅读（目标：15分钟）
- [ ] 核心词汇 3-5 个：
- [ ] 主旨概括（1句话）：

### 今日收获
>
```

---

## Step 4: 清理暂存 Inbox

// turbo
```powershell
Remove-Item "D:\personal\Obsidian\00-Inbox\Raw-Daily-Feeds.md"
```

---

## Step 5: 报告

告知用户：
- AI 日报已暂存至 `30-Daily/AI-News/AI-Daily-{date}.md`（含 7 项精选）
- IELTS 预览（听力 + 阅读）已暂存至 `30-Daily/IELTS-Preview/IELTS-Preview-{date}.md`
- ⚠️ 两份文件均**不含 wikilinks，不会出现在 Graph View** 中
- 学习完成后，说「**提交今日日报**」或运行 `/pkm-commit-daily` 正式收录
