# Quickstart — 5 Minutes to Your First PKM Run

This guide gets you from zero to your first automated Obsidian feed in under 5 minutes.

## Prerequisites

- Python 3.10+
- An Obsidian vault (any existing vault works)

## Step 1: Clone & Install

```bash
git clone https://github.com/yourusername/obsidian_workflow_open.git
cd obsidian_workflow_open
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

## Step 3: Run Doctor

```bash
python main.py --doctor --doctor-skip-network
```

If you see ✅ **OK**, proceed. Otherwise follow the Doctor's guidance.

## Step 4: Dry Run (Preview)

See exactly what files would be created without touching your Vault:

```bash
python main.py --dry-run
```

## Step 5: Fetch!

```bash
python main.py
```

Your Vault will now have new notes under `30-Daily/AI-News/`, `20-Sources/Papers/`, and `20-Sources/Videos/`.

---

## Next Steps

- [Configuration Reference](configuration.md) — customize your RSS feeds and YouTube channels
- [Note Templates](templates.md) — change frontmatter style
- [Writing Plugins](plugins.md) — add Reddit, Twitter, or any custom source
