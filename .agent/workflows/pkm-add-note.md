---
description: PKM Add Note — process user-uploaded content and write to Obsidian knowledge base using STAR+RISE framework
---

# PKM: 整理知识并写入 Obsidian (STAR + R-I-S-E 高阶框架)

当用户说「整理进知识库」、「加入知识库」、「记录这个」时，执行本工作流。

## Step 1: 分析并提取元数据

分析用户提供的原始材料，识别：
- **主题 / 标题**（简洁中文或英文，不超过 20 字）
- **知识领域**（从以下选择：python / cpp / r / ai-stack / math-modeling / data-science / research / ielts-listening / ielts-reading / ielts-writing / ielts-speaking）
- **语言**（cn 或 en）
- **关键标签**（3-5 个）

## Step 2: 使用 STAR + R-I-S-E 框架提炼内容

作为内容提炼专家，你必须打破"泛泛而谈"的总结习惯，深入推敲素材本质，必须保留所有的**核心代码**、**关键公式**（LaTeX格式）以及**精确参数**。

要求按以下结构生成 Markdown 内容：

### 1. STAR 结构提取 (骨架还原)
- **Situation (背景/上下文)**：问题存在的背景，为什么需要这个技术/思路？
- **Task (核心问题)**：面临的具体痛点、任务目标是什么？
- **Action (解决方法/模型)**：具体使用了什么算法、架构或代码实现？（**必须原样保留代码块和公式**）
- **Result (结论/评估)**：达成了什么效果？有什么数据指标证明？

### 2. R-I-S-E 多层分析 (认知升华)
- **Reflect (思考启示)**：这个方案打破了什么常规思维？
- **Implicit (深层隐含意义)**：表象技术之下，更底层的设计哲学是什么？
- **Synthesis (跨领域融合)**：这个思路能否迁移到其他领域（如用在不同系统的设计上）？
- **Execution (我该如何运用)**：总结出 1-3 条极具实操性的"避坑指南"或"复用清单"（带代码/公式）。

## Step 3: 调用 pkm_bridge.py 写入

使用终端运行以下命令将生成的高质量笔记写入 Obsidian（确保 content 的引号正确转义）：

// turbo
```bash
cd d:\Agent_programs
python pkm_bridge.py \
  --title "笔记标题" \
  --content "生成的 STAR + R-I-S-E 笔记正文" \
  --domain "知识领域" \
  --tags "标签1,标签2,标签3" \
  --language "cn或en" \
  --source "User Input"
```

## Step 4: 确认写入
告知用户：笔记已按 STAR+RISE 框架高优重构，并确保存入了指定的 Vault 路径中。
