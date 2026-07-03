---
name: forgetforge
description: Recall-centric memory for agent sessions. Use when prior facts are needed, context grows, or the user tags #keep_forever or #forget_this.
---

# ForgetForge

Call the `forgetforge` CLI. The plugin returns JSON contracts; the host agent owns model calls and final answers.

## Setup check

```bash
forgetforge check
```

## Common actions

| Situation | Command |
| --- | --- |
| New fact or decision to store | `forgetforge store <id> --content "<fact>"` (`--content-file <path>` or `--content -` for large input) |
| Session start or large context | `forgetforge status` |
| Prior fact needed | `forgetforge recall <topic>` |
| User says `#keep_forever` | `forgetforge keep <id>` |
| User says `#forget_this` | `forgetforge forget <id>` |
| Preprocessing or handoff brief received | `forgetforge import-brief --source manual --brief "<brief>"` (`--brief-file <path>` or `--brief -` for large input) |
| Diagnostics needed | `forgetforge doctor --json` |

Rules:

1. Read recall JSON before using a memory in your response.
2. Recall returns at most 20 matches by default.
3. If recall returns no matches, do not invent remembered facts.
4. Default recall searches `forget_requested = 0`; soft-forgotten cold memories are excluded and recoverable with `forgetforge list-forgotten` + `forgetforge unforget <id>`. Pruner cold-tier rows still recall while active in DB.
5. If store returns `contradiction_warnings`, tell the user what conflicts and ask how to reconcile.
6. `forgetforge doctor --json` on an uninitialized home reports `degraded` and exits 1 by design.
7. Hermes also exposes the same behavior through `forgetforge_*` tools and hot-context injection.
8. Slash commands available in Codex and Claude Code: `/forgetforge-recall`, `/forgetforge-status`, `/forgetforge-doctor`.
