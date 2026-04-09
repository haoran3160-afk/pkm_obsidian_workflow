---
description: PKM Add Note - process user-uploaded content and write to Obsidian knowledge base
---

# PKM: 整理知识并写入 Obsidian（AI Focus）

当用户说“整理进知识库”“加入知识库”“记录这个”时执行本流程。

## Step 1: 提取元数据

识别并确认：
- 标题（简短明确）
- 知识领域（`python` / `cpp` / `r` / `ai-stack` / `math-modeling` / `data-science` / `research`）
- 语言（`cn` / `en`）
- 标签（2-5 个）

## Step 2: 结构化内容

输出 Markdown 时保持以下结构：
- 背景与问题
- 关键方法（保留代码/公式）
- 结果与结论
- 可执行要点（1-3 条）

## Step 3: 写入 Obsidian

```bash
cd d:\Agent_programs\obsidian_workflow_open
python pkm_bridge.py \
  --title "笔记标题" \
  --content "结构化后的正文" \
  --domain "ai-stack" \
  --tags "AI,workflow,notes" \
  --language "cn" \
  --source "User Input"
```

## Step 4: 回报结果

告诉用户写入路径、核心标签、建议关联的 MOC 页面。
