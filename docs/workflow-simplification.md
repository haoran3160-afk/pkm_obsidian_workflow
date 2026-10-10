# Workflow simplification

The public repository keeps one supported path:

    approved sources
      → collection and evidence
      → GPT-6 Luna Generator
      → independent GPT-6 Luna Reviewer
      → deterministic renderer
      → create-new Obsidian note

The following are intentionally outside the runtime graph:

- old root-level Python entrypoints and UI/API writer;
- DeepSeek and provider-specific model code;
- Rust/native, AppContainer/WFP and activation experiments;
- old Codex task installers and multi-generation migration helpers;
- historical curation and formatter implementations.

The repository does not attempt to prove a Codex host session cryptographically. The
scheduler creates the two fresh Luna contexts; Python checks the useful deterministic
invariants: different sessions, correct model, complete claim decisions, matching
hashes/evidence IDs/reason codes, and publish-only-after-review.

Necessary safety remains deliberately small: bounded source collection, evidence
mapping, one editorial correction, process lock, create-new publication, and
fail-closed behavior on missing evidence or mismatched artifacts.
