# Quickstart

这份指南把第一次跑通拆成两条路线：

- `CLI-only`：只使用 Python 工作流
- `Local UI`：启用本地控制面板

## Prerequisites

- Python 3.10+
- Node.js 20+（仅控制面板需要）
- 一个可写的 Obsidian Vault

## 1. Clone

```bash
git clone https://github.com/haoran3160-afk/pkm_obsidian_workflow.git
cd pkm_obsidian_workflow
```

## 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

如果你也要跑测试和静态检查：

```bash
pip install -r requirements-dev.txt
```

## 3. Configure `.env`

- macOS / Linux: `cp .env.example .env`
- PowerShell: `Copy-Item .env.example .env`

至少设置：

```dotenv
OBSIDIAN_VAULT_PATH=D:/path/to/your/Obsidian
```

如果你要启用最终文案精修：

```dotenv
PKM_ENABLE_LLM_DIGEST_COPY=1
OPENAI_API_KEY=...
PKM_CURATION_MODEL=gpt-5.4-mini
PKM_CURATION_REASONING_EFFORT=medium
```

## 4. Run Doctor

```bash
python main.py --doctor --doctor-skip-network
```

如果这里不是 `OK`，先不要继续跑真实流程。

## 5. Dry Run

```bash
python main.py --dry-run
```

预期：

- 命令成功退出
- 输出会显示将要写入的 `AI-Daily-YYYY-MM-DD.md`
- 不会真的改动你的 Vault

## 6. Real Run

```bash
python main.py
```

默认最终产物：

- `00-Inbox/Raw-Feeds/Raw-Daily-Feeds-YYYY-MM-DD.md`
- `30-Daily/AI-News/AI-Daily-YYYY-MM-DD.md`

## 7. Optional: Start the Local UI

安装前端依赖：

```bash
npm install
npm --prefix ui install
```

启动本地控制面板：

```bash
npm run dev:full
```

默认地址：

- UI: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`

## Next

- [Local UI Guide](local-ui.md)
- [HTTP API](api.md)
- [Workflow Walkthrough](workthrough.md)
- [AI Daily Sample](sample_outputs/ai-daily-brief-sample.md)
