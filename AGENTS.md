# Personal AI Daily

Current workflow: Python collection → Luna Generator → independent Luna Reviewer
→ deterministic Markdown → create-new Obsidian publication.

Read docs/ai-daily-luna.md for the three stage commands and content contract.
The Codex scheduled task runs in D:\personal\obsidian_workflow and invokes this
checkout by absolute path. Both content roles use gpt-6-luna / max.

## Required behavior

- Mon/Thu: research + AI practice; Tue/Fri: builders + venture; Wed/Sat:
  cognition + GitHub. Request two daily modules. Sunday: select a small number
  of cross-domain weekly signals from six internally screened interests, with
  no additional daily. Follow returned edition and packet; no fixed weekly lanes.
- Review each story independently. If a requested module remains unapproved
  after the editorial correction, publish the approved stories as a partial
  daily and identify omitted modules. If none pass, publish nothing. Never fill
  omitted modules or weaken review.
- Keep author attribution, source dates, evidence bindings and independent review.
- GitHub recommendations need dated observed Star counts and concrete self-use
  scenarios. Personal recommendation stars express reader priority, not quality.
- Research explains the paper's gap, method, experimental scope, lineage and open
  questions. Never substitute institutional news or invent venue acceptance.
- A rejected weekly signal is omitted; publish accepted findings only. If none
  pass, return an explicit incomplete status. Never fill with invented material.
- Existing source catalog/context remain the source of configuration.
- No DeepSeek, OpenAI API, Codex CLI, Rust/AppContainer/WFP or activation framework.

## Data boundaries

- Do not overwrite existing Vault notes or modify .obsidian. Do not scan unrelated
  Vault content. Publisher targets only 30-Daily/AI-Daily-YYYY-MM-DD.md.
- Preserve uncommitted/untracked user assets and private configuration.
- The user authorized removing unused tracked engineering code on 2026-09-08.
  Deleted native/provider/qualification modules are recoverable from Git history.
  See docs/workflow-simplification.md; this does not authorize deleting notes,
  private data, runtime evidence, or unrelated assets.

## Development

Use small explicit commits. Separate behavior-preserving simplification from
content-contract changes. Verify the actual six-module output as well as tests.
Run pytest for affected behavior, Ruff on src/tests and Mypy on src/pkm_workflow.
Frozen historical docs under docs/agentic describe earlier designs and are not
instructions to reintroduce retired components.

The clean development baseline is https://github.com/haoran3160-afk/AI_Daily_workflow.
This checkout is the existing local deployment; private configuration and runtime
remain external and must never be pushed with the public project.
