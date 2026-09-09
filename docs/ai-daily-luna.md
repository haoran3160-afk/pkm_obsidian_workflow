# Personal AI Daily / Weekly — operating contract

Use the existing private Codex project and its local engine deployment. Content
generation and independent fresh-context review use gpt-5.6-luna / medium only.
No DeepSeek/OpenAI API, Codex CLI, provider fallback or automatic recharge.

## Reading cadence

| Day | Output |
| --- | --- |
| Monday / Thursday | Research + AI practice |
| Tuesday / Friday | Builder + AI venture/industry |
| Wednesday / Saturday | Cognition/growth + GitHub |
| Sunday | Weekly synthesis across all six modules; no additional daily |

Every daily contains exactly two distinct scheduled modules, one story each.
Previously published original URLs/projects are excluded from later daily choices.
Same-batch event duplication continues to use existing curation. Weekly review of
already read material is deliberate: connect observations instead of copying daily
paragraphs or presenting them as new recommendations.

Research remains Agent/harness-led: Monday is the anchor, Thursday rotates Agent,
RL and deep-learning exploration across weeks. Keep actual paper evidence, lineage,
gap, method, experimental limits and tentative research questions. Preserve light
icons, personal priority (not a science score), and dated GitHub stars/self-use fit.

## One scheduler, three stages

The existing local automation starts at 08:00 Shanghai. Successful publication
happens after processing, not necessarily at 08:00. The host and Codex app must be
available. Do not add another scheduler or Windows Task.

The deployment runs its absolute Python and scripts/run_workflow.py paths:

```powershell
python scripts/run_workflow.py --workflow ai --mode production --stage prepare
python scripts/run_workflow.py --workflow ai --mode production --stage review --run-id RUN_ID
python scripts/run_workflow.py --workflow ai --mode production --stage finalize --run-id RUN_ID --confirm-vault-write
```

Prepare selects daily or weekly from the Shanghai date. Later stages infer the
edition from the frozen run. For a manual weekly preview, add --edition weekly and
--mode shadow; weekly production is restricted to Sunday. Daily files retain
AI-Daily-YYYY-MM-DD.md; weekly files use AI-Weekly-YYYY-MM-DD.md (Sunday date), both
under the approved 30-Daily directory. Receipts/backing for daily and weekly are
separate, using the same publisher and lock; a weekly never replaces a daily.

On GENERATOR_READY read only returned input/instructions/schema. Write the draft
with the real role identity, not a model name. On REVIEWER_READY delegate exactly
one fresh-context Luna Reviewer; explicitly provide its real tool-returned agent
identity. It reads only the specified review packet and writes its own decisions.
Never author or rewrite another role's review. Python owns URLs, rendering and
publication. Only vault_write=true means newly written; ALREADY_EXISTS skips with
zero content generation. No existing note is automatically overwritten.

There is one shared structure/editorial repair opportunity. Follow returned paths,
keep prior files, rebuild review inputs after a draft correction and use a new
independent reviewer. Do not reset the budget or start another run to evade a
failure. An interrupted local deterministic write can resume once in the same run
without calling models again; mismatching artifacts remain a failure.

## Supply and cost

Daily collection touches only the requested modules: at most four candidates,
12,000 evidence characters. The health denominator covers due RSS relevant to
those modules, not untouched sources elsewhere in the catalog. Both modules must
have free verified evidence; missing supply is an explicit failure, not filler.
Preserve the approved source windows and dated unread-classic fallback.
Weekly-cadence RSS sources are checked on their module's first reading day
(Mon/Tue/Wed), so they are not stranded on the no-fetch Sunday summary.

Weekly uses only verified daily publications from Monday through the run date,
including their sealed evidence snapshots; it does not run another broad fetch.
Each module receives a bounded source-labelled bundle (at most three distinct
originals, normally two), then all six bundles enter one Generator/Reviewer cycle
within 24,000 characters. Every source retains its own date and original link.
Prior editorial judgments are marked as interpretations, not new source facts.
Only one source is enough for a single-case recap, never a claimed cross-day trend.
If one of the six modules lacks trustworthy material, do not manufacture a weekly.

New daily reports retain the selected bounded evidence and reviewed content in
durable reports, so weekly generation does not depend on scratch survival. Older
published reports can use their exact hash-verified original run while it exists;
missing old evidence is reported, not reconstructed from memory or Vault scans.
Weekly reports never become daily reading history or inputs to later weeklies.

Daily normally uses one Generator and one Reviewer; a weekly uses the same pair
once for all six modules. Platform token usage unavailable means unknown, not free.
Do not modify code/config during scheduled runs, scan unrelated Vault content,
change .obsidian, install recommended projects, or publish runtime/private data.
