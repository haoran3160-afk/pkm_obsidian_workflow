## Summary
<!-- What does this PR do? One sentence. -->

## Motivation
<!-- Why is this change needed? Link to a related issue if applicable. Closes #XXX -->

## ETL Layer Changed
- [ ] Extract (`fetcher.py` / plugin)
- [ ] Transform (`formatter.py` / templates)
- [ ] Load (`writer.py`)
- [ ] Orchestrator (`main.py`)
- [ ] Config (`config_schema.py` / `pkm_config.json`)
- [ ] Docs / Tests only

## Checklist
- [ ] I have run `pytest` locally and all tests pass
- [ ] I have run `ruff check .` with no new errors
- [ ] If I changed `fetcher.py`, new network calls use `@retry` from `tenacity`
- [ ] If I changed `formatter.py`, all functions remain pure (no I/O side effects)
- [ ] If I changed `writer.py`, only this module performs Vault/API writes
- [ ] If I added a new config key, I updated `config_schema.py` and `.env.example` / `pkm_config.json`
- [ ] `README.md` updated if the CLI interface or setup steps changed

## Testing
<!-- Describe how you tested this. Include `python main.py --test` output if relevant. -->

## Screenshots / Logs
<!-- Optional: attach terminal output or log snippets. -->
