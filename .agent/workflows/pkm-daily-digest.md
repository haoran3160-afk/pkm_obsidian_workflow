---
description: PKM Daily Digest - agentic curation for AI news, papers, and videos
---

# PKM: 每日 AI 资讯策展（Main Agent Workflow）

当用户说“每日拉取”“今日资讯”“更新知识库”时执行本流程。

## Step 1: 生成原始输入

```powershell
cd d:\Agent_programs\obsidian_workflow_open
python main.py --raw-only
```

输出到：
- `00-Inbox/Raw-Feeds/Raw-Daily-Feeds-{YYYY-MM-DD}.md`

## Step 2: AI 资讯筛选

按以下优先级精选：
- coding-agent / evaluation / context engineering / tool calling / memory
- 有方法细节、可迁移实践、可验证结论

降低优先级：
- 纯融资/公关/活动预告
- 无可执行细节的快讯

## Step 3: 生成日报

日报应包含：
- `Fast Lane (60s)`：一屏速读
- `Top Picks`：高分条目（why it matters + summary）
- `Distill Queue (CODE)`：可执行整理任务
- `Knowledge Map`：Mermaid mindmap
- `Deferred Queue`：延后阅读队列

输出到：
- `30-Daily/AI-News/AI-Daily-{YYYY-MM-DD}.md`

## Step 4: 落地源笔记

按全局限流写入：
- 论文：`20-Sources/Papers/`
- 视频：`20-Sources/Videos/`

## Step 5: 回报

告知用户：
- 写入文件数量
- 论文/视频候选与 deferred 数量
- 日报路径与关键 Top Picks
