---
description: PKM Commit Daily - promote reviewed AI daily content to permanent notes
---

# PKM: 提交今日 AI 内容到正式 Vault

当用户说“提交今日日报”“收录今天”“我看完了”时执行本流程。

## Step 1: 确认今日日报

读取并确认：
- `30-Daily/AI-News/AI-Daily-{TODAY}.md`

如果用户未确认已阅读，先询问“今天的 AI 日报要全部提交还是只提交部分条目？”

## Step 2: 升级为正式笔记

对用户指定条目：
- 更新 frontmatter：`status: reviewed`
- 增加 `[[wikilinks]]`（关联 MOC / 项目页）
- 生成可长期复用的结构化正文（问题/方法/结论/行动）

## Step 3: 写入目标目录

建议落地路径：
- `20-Sources/Articles/`
- `10-Notes/Programming/AI-Stack/`
- `10-Notes/Research/`

## Step 4: 回报结果

告诉用户：
- 新增了哪些正式笔记
- 每条笔记的目标路径
- 新增了哪些关联链接
