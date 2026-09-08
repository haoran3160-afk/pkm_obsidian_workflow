# AI Daily with Luna

Run from the current Codex project, D:\personal\obsidian_workflow. The engine
remains in D:\Agent_programs\obsidian_workflow_mvp. Use GPT-5.6 Luna with medium
reasoning for both content roles. This runbook replaces the old DeepSeek daily
command; historical providers and workflows remain frozen.

## Daily operation

Use this exact Python and script for every stage:

    & 'D:\Agent_programs\obsidian_workflow_mvp\.venv\Scripts\python.exe' 'D:\Agent_programs\obsidian_workflow_mvp\scripts\run_workflow.py' --workflow ai --mode production --stage prepare

1. Read the returned JSON. ALREADY_EXISTS or a terminal no-news result ends the
   run. PRODUCTION_BUSY ends the run without another attempt. Other failures
   are reported with their error code and file path.
2. On GENERATOR_READY, read only generator_input_path, instructions_path, and
   schema_path. The task's Luna writes its own draft envelope to draft_path
   using create-new. session_id is the real Codex task/session ID, never an
   invented UUID. Use the current session metadata; if unavailable, report
   ROLE_SESSION_ID_REQUIRED.
3. Run the same command with --stage review --run-id RUN_ID.
4. On REVIEWER_READY, spawn exactly one new subagent with model
   gpt-5.6-luna, reasoning medium, and no conversation history (fork_turns=none
   where available). Give it only the returned review_input_path,
   instructions_path, schema_path, review_path and the instruction to read
   those files and write its own review envelope. It must use its real
   session ID and copy hashes from the packet. The parent must not author,
   edit, or fill in Reviewer decisions. If delegation/model selection is
   unavailable, stop with REVIEWER_UNAVAILABLE; do not self-review.
5. Wait for the Reviewer to finish. Run the same command with --stage finalize
   --run-id RUN_ID --confirm-vault-write.
6. Inspect the final JSON and the resulting Markdown once. Report only failures
   by notification; successful task output may briefly link the written file.

Use --mode shadow at prepare/finalize for an evaluation without Vault writes.
A successful shadow can be promoted by running finalize again in production
mode with --confirm-vault-write, using the same run ID and reviewed artifacts.

## One bounded repair

Only SCHEMA_REPAIR_REQUIRED authorizes one role-output structure repair, at
the returned output_path. Preserve the original file. Supply the failing
role with its original instructions, schema, input and the explicit error.
A Reviewer repair stays with that Reviewer session. If the Generator is
repaired, run review again to produce the frozen input before spawning a new
Reviewer. Fact rejection is not a repair request; do not rewrite facts until
they are accepted. SCHEMA_REPAIR_EXHAUSTED ends the run.

prepare --run-id RUN_ID explicitly resumes an interrupted same-day run and
returns its current stage. Ordinary scheduled runs never guess a run ID,
delete reservations, or automatically regenerate after failures.

## Content and scope

Keep the approved source catalog, free-body requirements, interests,
7/14/30-day windows and candidate limits. Aim for four or five worthwhile
stories, but never fill a quota. Use natural Chinese explanations and bounded,
specific judgments. Preserve source attribution. At most one action,
grounded in an approved project, prior knowledge or note anchor.

Every material claim needs the independent Reviewer's ACCEPT. Reviewer sees
the same bounded evidence as Generator. Python alone maps URLs, renders
Markdown and publishes. No direct Vault editing, no .obsidian changes,
no other Vault scans, no code edits during a scheduled run, no API keys,
DeepSeek calls, OpenAI API calls, Codex CLI, or automatic recharge.

All role inputs and outputs are retained in the run directory. Published
reports/receipts are durable. Actual Codex token usage is null if the platform
does not expose it; api_request_count=0 is not a claim of zero Codex usage.
session_id/model are provenance declarations cross-checked against real task
activity during acceptance, not cryptographic proof against a malicious local
agent. Code-level schema checks cannot establish semantic truth by themselves.

## Verification

Run tests/test_ai_daily_luna.py plus collection, shadow, production and provider
regressions. Check Ruff and Mypy for touched source files and perform a full
pytest run before rollout. The runtime command itself is not an LLM SDK:
there are three bounded file handoffs, no model HTTP service or background
worker. Existing lock, evidence helpers, renderer and publisher are reused.

## Historical source state

The source catalog contains configured and cataloged entries; only active
adapters are fetched. This migration does not claim all 100 catalog entries
are live. Seven-day daily deduplication reads verified published reports;
shadow previews are not treated as read news. Legacy used-article history
remains available without being overwritten.

## First live acceptance — 2026-09-08

Run 65115bfb3b344c9cb883d64e920e7d5d used two real Luna agents and one structural
Generator repair. Four drafted stories became two published stories: METR was
removed for missing the second required deep-read mechanism; the Astra importance
claim received Reviewer ABSTAIN. Neither was rewritten to obtain acceptance.
The published brief covers Latent Space's AEO study and Tony Dinh's game-building
experience. It is a short first sample, not evidence of broad five-pillar coverage
or better quality than DeepSeek. Previous samples use different evidence dates.

Publication reused the already reviewed artifact. Production prepare then returned
ALREADY_EXISTS with no model generation and no further Vault write. The complete
suite passed 922 tests with 143 pre-existing conditional skips; Ruff and Mypy passed.
See runtime durable/receipts/ai-daily/2026-09-08.published.json and its bound report.
