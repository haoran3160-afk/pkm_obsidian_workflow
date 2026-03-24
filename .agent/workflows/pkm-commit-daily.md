---
description: PKM Commit Daily — Promote reviewed daily content to permanent Vault notes with wikilinks
---

# PKM: 提交今日内容进正式 Vault

当用户说「提交今日日报」、「收录今天」、「我看完了」时执行本工作流。

> 这一步的作用：将你已经学习/阅读过的 Daily 暂存内容升级为**带 `[[wikilinks]]` 的正式笔记**，使其出现在 Graph View 中，与对应 MOC 建立连接。

---

## Step 1: 确认今日 Daily 内容

读取以下两个文件，确认用户已完成打卡（检查 `status: pending-review` 是否需要改变）：

- `D:\personal\Obsidian\30-Daily\AI-News\AI-Daily-{TODAY}.md`
- `D:\personal\Obsidian\30-Daily\IELTS-Preview\IELTS-Preview-{TODAY}.md`

询问用户（如果不清楚）：**"今天的 AI 日报和 IELTS 材料，你都学完了吗？或者选择性提交？"**

---

## Step 2: 提交 IELTS 材料到正式 Vault

将今日 IELTS-Preview 内容**升级**为正式 IELTS 笔记，写入：
`D:\personal\Obsidian\10-Notes\IELTS\{技能类型}\IELTS-{主题}-{YYYY-MM-DD}.md`

正式笔记需要：
1. **更新 frontmatter**：`status: reviewed`，`type: permanent-note`
2. **添加 wikilinks**：
   - 底部加 `[[MOC-IELTS]]`
   - 如有相关主题笔记，添加关联链接（如 `[[Environment-Vocab]]`）
3. **保留用户填写的打卡内容**（学到的词汇、主旨概括）

```bash
# 写入命令示例
D:\anacoda\python.exe d:\Agent_programs\pkm_bridge.py \
  --title "IELTS {类型} - {主题} - {date}" \
  --content "升级后的正式笔记内容（含 wikilinks）" \
  --domain "ielts-{listening/reading}" \
  --tags "IELTS,{主题分类}"
```

---

## Step 3: 提交 AI 日报摘要（可选）

如果用户选择将某篇 AI 资讯提炼为正式笔记，使用 STAR+RISE 框架重新整理，写入：
`D:\personal\Obsidian\20-Sources\Articles\{source}-{title}-{date}.md`

正式笔记底部需包含：`[[MOC-Research]]` 或 `[[MOC-Data-Science]]` 等 wikilink。

---

## Step 4: 确认并报告

告知用户今天有哪些内容被正式收录进 Vault（哪些出现在 Graph View 了）。日报文件本身仍保留在 `30-Daily/`，不做删除。
