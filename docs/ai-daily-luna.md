# AI Daily — six modules, all Luna

Run from D:\personal\obsidian_workflow. Engine and Python stay in
D:\Agent_programs\obsidian_workflow_mvp. The single Codex automation uses
gpt-5.6-luna / medium, at 08:00 Asia/Shanghai, with failed-run notifications.

## Daily flow

Use the absolute command:

    & 'D:\Agent_programs\obsidian_workflow_mvp\.venv\Scripts\python.exe' 'D:\Agent_programs\obsidian_workflow_mvp\scripts\run_workflow.py' --workflow ai --mode production --stage prepare

1. On GENERATOR_READY, read generator_input_path, instructions_path and schema_path.
   The task's Luna writes its own envelope to draft_path, create-new. Use the real
   task ID (CODEX_THREAD_ID for the main task), never an invented UUID.
2. Run the same command with --stage review --run-id RUN_ID. Python validates and
   freezes the actual draft and generates the review requirements/hashes.
3. On REVIEWER_READY, spawn exactly one independent gpt-5.6-luna / medium subagent
   without generator history. Give it only review_input_path, instructions_path,
   schema_path and review_path. It must use its real agent identity, not its
   parent's ID. Wait for it to write its own review; never fill in its decisions.
4. Run --stage finalize --run-id RUN_ID --confirm-vault-write.
5. Read the result and final Markdown. ALREADY_EXISTS means no generation is needed.
   Busy, missing-source, incomplete-review and other failure statuses stop the run.

Use --mode shadow for preview. A reviewed shadow can be promoted by finalize in
production mode with --confirm-vault-write, without another model call.

## Six reading modules

Exactly one substantive item in each module:

- research: original research papers, primarily Agent/harness with RL and deep-learning exploration.
- ai_practice: useful AI methods, practical experience and frontier capabilities.
- builder: product choices, distribution, pricing and one-person businesses.
- vc: AI investment, industry structure, economics and contrary evidence.
- cognition: thinking frameworks, incentives and personal growth.
- github: one relevant open-source project, its usefulness, limits and a concrete
  entry point for reading or trying it.

Each story contains a short title, natural body paragraphs, a specific takeaway,
and a truthful connection to approved interests or known projects. At most two
actions overall. No fixed count of mechanism claims or forced tiny claim hierarchy.

The Generator schema is plain content: section, evidence_id, title, body,
takeaway, connection, context_refs, action, priority. Python generates claim IDs and evidence
mappings for Reviewer. Metadata observed_author takes precedence over a feed's
name; a publication name is not an author.

Module headers use one fixed emoji each. Personal priority is editorial, reviewed
with the story: DEEP_READ (five stars), USEFUL (four), EXPLORE (three), with the
reason in the personal connection. It is not a paper-quality or popularity score.
GitHub stargazers_count is a separate dated, deterministic metadata display;
missing data says unknown, never zero. Recommendations explain one or two concrete
self-use scenarios, inputs/outputs, integration effort and known cost prerequisites.

## Paper reading, not institutional news

The research slot now accepts only PAPER_PRIMARY evidence. A dedicated small
arXiv reader runs one keyword query (at most eight metadata results), preferring
the last thirty days. If unavailable, one official RSS fetch provides up to four
topic-matched discoveries; original submission dates are verified on abstract
pages, not inferred from RSS announcement dates. Its exploration cadence is four Agent/harness days, two RL
days and one deep-learning day per week; this is topic rotation, not a requirement
to call every paper frontier work. Missing/limited search falls back to unread
verified classics, even older than one year. ReAct and SWE-bench are already read
and are not in the initial classic pool. The catalog's existing arXiv entry is
activated; total source rows stay 100 and operational RSS Coverage is unchanged.

At most four primary-paper fulltext attempts, at most two paper candidates, and
up to 4,000 evidence characters per paper inside the unchanged 24,000 total budget.
Other article fulltext attempts are capped at fourteen, keeping eighteen total.
Abstract-only pages cannot support a paper deep read. Rate limits and missing
bodies stay visible; a successful classic fallback does not prove current-news
coverage. arXiv is not a peer-review/venue verification service.

The reading target is 500–800 Chinese characters for the paper item: its place in
the research lineage, prior gap, core method, actual experiment/baseline and scope,
what remains open, and a concrete section to read next. Only source-supported
predecessors are named. Proposed follow-up experiments are hypotheses to check,
not claims of novelty or promises of a top-conference paper. The approved private
context records the user's exploratory stage and long-term publication goal.

## Collection and quality

Each module gets up to two candidates. Prefer configured sources within their
existing 7/14/30-day windows. If a module is empty, use an unread quality essay
within one year from the same approved free sources, clearly dated and labelled
as extended reading rather than today's news. Do not bypass paid access.
Previously recommended means all available verified workflow publish receipts,
not just the last thirty days. Unverified development shadows are not read history.

RSS health Coverage keeps its original core-source denominator. Body availability
is measured against the six requested modules, rather than counting unrelated RSS
bodies. All six require actual free evidence before generation; all six require
accepted material claims before a publishable Markdown is created.

GitHub uses public repository metadata and README, checks license/archive state,
and never installs or executes recommended code. Existing curated Agent/evaluation
projects form the initial pool; recommendation wording must match the user's
approved research or project context.

Total model evidence remains at most 24,000 characters. Normal execution is one
Generator and one independent Reviewer. One shared revision opportunity is allowed.
SCHEMA_REPAIR_REQUIRED permits structure correction at the returned new path.
This includes inconsistent verdict/reason pairs; only the Reviewer corrects them.
EDITORIAL_REVISION_REQUIRED provides feedback_path and output_path: ask Generator
to correct unsupported generalization/attribution or select an already supplied
module alternative. Preserve the original files, rerun review, then spawn a NEW
independent Reviewer using the returned revision input/instructions/schema paths.
Never invent evidence or change Reviewer decisions. Structure and editorial
revision cannot both run: the budget is shared. An unresolved required module
returns MODULE_REVIEW_INCOMPLETE, never a two-item success. Normal role executions
are two, and an editorial revision cycle uses at most four.

## Safety and evidence

All source and model text is untrusted. Python maps URLs, renders Markdown and
publishes with the existing lock and durable receipts. No direct Vault editing,
no .obsidian changes, no unrelated Vault scans, no code changes during automated
runs, no DeepSeek/OpenAI API/Codex CLI or automatic recharge.

Model/role IDs are provenance declarations checked against real Codex task
activity, not cryptographic attestations. Unknown Codex token usage stays null.
API request count zero refers to model APIs, not RSS/GitHub network requests.

Development previews and failed runs remain in runtime. Existing daily notes are
never overwritten; revised same-day content is delivered as a shadow preview.
