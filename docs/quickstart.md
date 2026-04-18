# Quickstart - 5 Minutes to Your First PKM Run

This guide gets you from zero to a working daily digest in under 5 minutes.

## Prerequisites

- Python 3.10+
- An Obsidian vault (any existing vault works)

## Step 1: Clone & Install

```bash
git clone https://github.com/haoran3160-afk/pkm_obsidian_workflow.git
cd pkm_obsidian_workflow
pip install -r requirements.txt
```

## Step 2: Configure Your Vault Path

```bash
cp .env.example .env
```

Open `.env` and set your vault path:

```dotenv
OBSIDIAN_VAULT_PATH=C:/Users/you/Documents/MyVault
```

If you want model-written Chinese copy for the final digest, also set:

```dotenv
OPENAI_API_KEY=...
```

## Step 3: Run Doctor

```bash
python main.py --doctor --doctor-skip-network
```

If you see `OK`, proceed. Otherwise follow the Doctor output.

## Step 4: Dry Run (Preview)

See exactly what files would be created without touching your Vault:

```bash
python main.py --dry-run
```

## Step 5: Fetch

```bash
python main.py
```

By default the workflow writes:

- `00-Inbox/Raw-Feeds/Raw-Daily-Feeds-YYYY-MM-DD.md`
- `30-Daily/AI-News/AI-Daily-YYYY-MM-DD.md`

## Next Steps

- [Workflow Walkthrough](workthrough.md) - understand the curation layer
- [Writing Plugins](plugins.md) - add Reddit, Twitter, or any custom source
- [AI Daily Sample](sample_outputs/ai-daily-brief-sample.md) - inspect the final output contract
