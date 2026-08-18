# PKM Workflow v3 entrypoint attribution

Status date: 2026-07-26 (Asia/Shanghai)

Scope: the PKM workflow only

Evidence contract: E00.4
Verification evidence ID: `03ef9108-e793-4de8-8bac-5ea9ecb405ea`

## Decision

The 2026-07-26 legacy Raw/state write window is attributed to the existing
Windows Task `\PKM-Daily-Fetcher`. The task is still `Ready` and therefore is
an active migration risk. No write in the inspected window remains classified
as `UNATTRIBUTED_WRITE`, but Gate A and E80 remain blocked until E00.6 pauses
the task in place and a subsequent observation window confirms that the legacy
writes have stopped.

This inventory is read-only. No entrypoint definition, file, source record, or
Vault note was changed or deleted while producing it.

## Observed entrypoints

| Kind | Stable object / observation | State | Attribution result |
|---|---|---|---|
| Windows Task | `\PKM-Daily-Fetcher` | `Ready`; next run observed as 2026-07-27 07:00 +08:00 | Owner of the 2026-07-26 17:10–17:13 legacy Raw/state write window. Its action invokes the private workflow's legacy `daily_fetch.py --raw-only` wrapper. |
| Codex automation | `pkm-daily-digest-curation` | `PAUSED`; daily 08:10 | Known legacy-capable definition. It references the private workflow config, legacy state, and Raw output, but did not own the inspected write window. |
| Codex automation | `pkm-weekly-digest` | `PAUSED`; Sunday 09:30 | Known legacy-capable definition. It references the private workflow config, legacy state, weekly output, and health check, but did not own the inspected write window. |
| Python scheduler | Process/command-line inspection | No matching resident process | No owner found and no unexplained write assigned to this kind. |
| Startup | User/machine Startup and Run-key inspection | No matching entry | No owner found and no unexplained write assigned to this kind. |
| Service | Windows service inspection | No matching service | No owner found and no unexplained write assigned to this kind. |
| Login script | Known login-entry inspection | No matching entry | No owner found and no unexplained write assigned to this kind. |
| Current process | Python/Node process inspection | No matching process | No owner found and no unexplained write assigned to this kind. |
| UI/API | Repository entrypoint plus process inspection | Code path exists; no active process observed | Not the owner of the inspected write window. It remains subject to the v3 writer and authorization boundary before use. |
| Manual log | Private workflow execution log | Legacy run recorded at 17:10–17:13 | Corroborates the Windows Task attribution through matching time, wrapper, Raw output, and state updates. |

## External references

Entrypoint definitions are part of the Source Universe observation boundary.
References to private config, source/state registries, workflow definitions,
interpreters, and output routes are recorded by identity and hash in the
private baseline; their private values are not copied into this open-repository
document. Any URL-like reference that cannot be resolved to an observed source
record is emitted as `unresolved_reference` and blocks release progression.

The E00.4 fixture suite proves both sides of that rule:

- a URL in an external entrypoint becomes an `unresolved_reference`;
- an unowned write becomes `UNATTRIBUTED_WRITE` and sets the report to
  `BLOCKED`.

## Required containment

E00.6 must preserve a CREATE_NEW pre-state snapshot, pause/disable the exact
Windows Task in place, preserve its stable object ID and complete definition,
and then capture a post-state snapshot. It must not unregister or delete the
task. The two Codex automations are already paused; their definitions still
need to be snapshotted and later replaced only through the approved release
protocol.

After containment, the observation window must show:

1. no legacy Raw/state write from any old entrypoint;
2. no new `UNATTRIBUTED_WRITE`;
3. any accidental invocation is shadow-only and has no formal Vault side
   effect.

Failure of any item leaves E00.6, E80, and Gate A blocked.
