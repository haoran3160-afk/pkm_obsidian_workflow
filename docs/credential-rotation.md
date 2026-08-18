# Credential binding and rotation

The workflow uses two logical credentials: `openai_llm` and the optional
`obsidian_rest_readback`. Disk-only operation with LLM features disabled
requires neither.

Production resolution order is:

1. an append-only binding to a versioned Windows Credential Manager target;
2. the controlled process variables `PKM_OPENAI_API_KEY` and
   `PKM_OBSIDIAN_REST_API_KEY`;
3. only in shadow mode, a legacy `.env` value with a
   `LEGACY_SECRET_SOURCE` warning.

The local CLI creates a short-lived challenge before binding. Binding requires
an interactive TTY, the exact challenge phrase, and the current Windows
identity. HTTP, Codex, Task Scheduler, and free-form secret arguments cannot
bind credentials. Binding events contain target hashes and identity hashes,
never secret values.

To rotate a key, the owner first adds a new versioned Generic Credential using
the Windows credential UI, then creates a new binding. Old binding events
remain immutable. Revoking a key at an external provider is a separate
Ask First action. Recovery likewise adds a new binding to a valid target;
it does not delete or rewrite prior files.
