# Personal AI Daily / Weekly — operating contract

Use the existing private Codex project and its local engine deployment. Content
generation and independent fresh-context review use gpt-6-luna / max only.
No DeepSeek/OpenAI API, Codex CLI, provider fallback or automatic recharge.

## Reading cadence

| Day | Output |
| --- | --- |
| Monday / Thursday | Research + AI practice |
| Tuesday / Friday | Builder + AI venture/industry |
| Wednesday / Saturday | Cognition/growth + GitHub |
| Sunday | Select cross-domain weekly signals from all six screened interests; no additional daily |

Every daily requests two distinct scheduled modules, one story each.
Previously published original URLs/projects are excluded from later daily choices.
Same-batch event duplication continues to use existing curation. Weekly review of
already read material is deliberate: connect observations instead of copying daily
paragraphs or presenting them as new recommendations.
Each story is reviewed independently. If one module still has no approved story
after the single editorial correction, publish the approved story as a partial
daily and name the omitted module near the top; never add an empty section or
weaken the review. If no story passes, do not create a note.

Research remains Agent/harness-led: Monday is the anchor, Thursday rotates Agent,
RL and deep-learning exploration across weeks. Keep actual paper evidence, lineage,
gap, method, experimental limits and tentative research questions. Preserve light
icons, personal priority (not a science score), and dated GitHub stars/self-use fit.

## One scheduler, three stages

The scheduler coordinates; it does not author either role output. Before fetching,
explicitly request gpt-6-luna/max fresh-context Generator and Reviewer agents.
Their initial readiness checks must not read evidence or write outputs. Use the
host-returned identities and explicit accepted model request, not a self-guessed
model name. If delegation is unavailable, stop before collection and report the
actual tool error. Feed evidence to the Generator only after prepare, and feed
only the sealed review packet to the already independent Reviewer after review.

An operator-approved migrate-model stage can migrate a same-day, prepared-only
Luna run from the pinned pre-Sol strategy. It preserves the original state/input,
appends current-model instructions/schema/state (legacy Sol filenames retained), and keeps both claims bound to the same
run. Published historical artifacts are not migrated. Other earlier strategies
return STRATEGY_CHANGED with their run ID, never silently refetch.
Started drafts/reviews/reports, unknown strategies and inadequate evidence
are rejected. Never use migration as a retry or repair-budget reset.
The legacy module/contract names remain compatibility identifiers, not model claims.

The existing local automation starts at 08:00 Shanghai when the host is online.
If missed, open Codex and click Run now on the same task once; this is not an
app-start trigger and does not use hourly polling. Successful publication
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

Collection has a separate bound: one initial attempt and at most one explicit
`prepare --run-id RUN_ID` retry after an I/O failure/timeout, on the same day.
This includes transient source failures reported by collection, not just exceptions
escaping the collector. Healthy zero yield, invalid data and unavailable/paid bodies
are not retryable. A temporary source failure matters only when coverage or a
requested module is missing; a successful evidence packet is never recollected.
When prepare returns COLLECTION_FAILED with retryable=true, the scheduled runner
must call prepare once with that run ID before generating. Never retry a terminal
shortage, reset the date claim, or start another run. This recovery happens before
model handoff and does not consume or reset the shared editorial repair opportunity.
The claim points to sealed collection state before fetching. Failed attempts
remain on disk; retrying never creates a new run or resets model/repair budgets.
Attempt markers and failure records must agree. Missing, damaged or mismatched
records return COLLECTION_ATTEMPT_STATE_INVALID instead of granting another fetch.
Calling prepare without a run ID reports the recorded collection failure rather
than retrying. Invalid collection data, exhausted attempts or partial model-input
files are not recollected. Once prepared, normal same-run resume performs no fetch.
Pre-upgrade failed runs without sealed collection state are not automatically
migrated; do not remove their claims or infer missing bindings to force a retry.

## Supply and cost

Daily collection touches only the requested modules: at most four candidates,
12,000 evidence characters. The health denominator covers due RSS relevant to
those modules, not untouched sources elsewhere in the catalog. Both modules must
have free verified evidence; missing supply is an explicit failure, not filler.
Preserve the approved source windows and dated unread-classic fallback.
GitHub selection ranks at most six unread repository metadata records by approved
project/prior-knowledge and interest terms before fetching README evidence. Stars
and date rotation do not determine relevance; only two verified candidates reach
the model. A metadata match is a selection hint, not proof of personal usefulness.
The recommendation must state one concrete use flow: task, input, documented
project capability, observable output, and why that output helps the user's
research or project. Mark untested use as a trial idea. Review may accept the
bounded connection when README evidence supports the capability and approved
context supports the user's interest; README need not name the user. Missing
either side or a vague use flow remains a reason to abstain.
Generator instructions contain common evidence rules plus only the requested
modules' editorial guidance; research requirements do not leak into cognition.
Weekly-cadence RSS sources are checked whenever their module is scheduled
(both paired reading days). There is no cross-day candidate cache; checking only
the first day would leave the second day's module empty.

Weekly considers the verified daily publications from Monday through Sunday and
their already prepared, free-body candidates. New daily reports retain bounded
candidate excerpts even when an item was not recommended. Older reports without
that snapshot contribute only published reading. Missing interest lanes may be
supplemented from approved free sources; a missing lane is recorded internally
and never forces a reader-facing section or blocks a credible weekly finding.
Deduplicate original URLs, screen all six interests, and send at most 12
source-level records within 24,000 evidence characters to one Generator and one
independent Reviewer. Every claim cites the original evidence IDs. A shared
pattern requires independent sources or an observed within-week development;
otherwise describe a bounded case. Do not claim acceleration across weeks.
Past editorial interpretations are selection cues, never primary facts.
Keep each original date, author and link. Mark newly recommended weekly reading,
including prior daily candidates that were not published, for future deduplication.
If no finding passes review, do not create a weekly note.

New daily reports retain the selected bounded evidence and reviewed content in
durable reports, so weekly generation does not depend on scratch survival. Older
published reports can use their exact hash-verified original run while it exists;
missing old evidence is reported, not reconstructed from memory or Vault scans.
Daily dedup and weekly inputs verify the durable prepared/published receipt pair
and its independent report snapshot, including their recorded publication binding.
They do not require the current Vault note or its hardlinked backing to stay
unchanged. Editing or deleting a note does not undo its publication history.
Live publication/reconciliation still verifies the current file and reports a
conflict after edits/deletion; it never overwrites or recreates that note.
Only newly introduced weekly URLs join daily deduplication. Weekly reports do
not become inputs to later weeklies.

Daily normally uses one Generator and one Reviewer; weekly uses the same pair
once for the globally selected findings. Platform token usage unavailable means unknown, not free.
Do not modify code/config during scheduled runs, scan unrelated Vault content,
change .obsidian, install recommended projects, or publish runtime/private data.
