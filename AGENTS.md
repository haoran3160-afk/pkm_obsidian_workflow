# Personal AI Daily

Current workflow: Python collection → Sol Generator → independent Sol Reviewer
→ deterministic Markdown → create-new Obsidian publication.

Read docs/ai-daily-luna.md for the three stage commands and content contract.
The Codex scheduled task runs in D:\personal\obsidian_workflow and invokes this
checkout by absolute path. Both content roles use gpt-5.6-sol / medium.

## Required behavior

- Mon/Thu: research + AI practice; Tue/Fri: builders + venture; Wed/Sat:
  cognition + GitHub. Exactly two daily modules. Sunday: six-module weekly
  synthesis from verified daily history, no extra daily. Follow returned edition
  and requested_sections; never fill unscheduled modules.
- Keep author attribution, source dates, evidence bindings and independent review.
- GitHub recommendations need dated observed Star counts and concrete self-use
  scenarios. Personal recommendation stars express reader priority, not quality.
- Research explains the paper's gap, method, experimental scope, lineage and open
  questions. Never substitute institutional news or invent venue acceptance.
- Missing module evidence or a rejected mandatory section returns an explicit
  incomplete status; never fill a section with invented or irrelevant material.
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
