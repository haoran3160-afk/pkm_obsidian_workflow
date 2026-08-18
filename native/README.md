# v7.3 native trust boundary — deferred experiment

> **Current decision:** this directory is retained as historical/experimental code and is not on the lightweight production critical path. Do not extend, activate, package, or reference these binaries from Task Scheduler unless the escalation conditions in `docs/agentic/decisions.md` are met and the user approves a new architecture decision.

The active target is a single Python orchestrator with a create-new `os.link` publisher. Native qualification is no longer a first-release gate. The text below records the former v7.3 design and its fail-closed state.

`pkm-stage0` and `pkm-broker` are fail-closed qualification binaries. They
provide strict command grammars and put themselves in a kill-on-close Windows
Job Object. Stage0 also freezes the same-directory `pkm-broker.exe` leaf, the
broker `run` argv, and the allowed exit-code propagation set. Its compile-time
qualification gate remains false, so the real process exits with safety code 3
before starting the broker. Broker `run` likewise remains fail-closed.

Production activation remains blocked until a later signed release adds and
qualifies all of the following on a disposable Windows VM/VHD:

- signed PromotionLedger selection and generation fencing;
- AppContainer/LPAC creation and WFP ALE default-deny rules;
- a package-SID-bound one-shot Codex proxy;
- A/B audit backing files, create-new NTFS hard links and directory flush;
- MAC journal recovery and real power-loss tests;
- Task bundle installation, synchronous exit-code propagation, and user
  activation approval.

No code in this directory falls back to the legacy Python writer.
