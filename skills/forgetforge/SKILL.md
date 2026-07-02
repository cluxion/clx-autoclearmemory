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
| New fact or decision to store | `forgetforge store <id> --content "<fact>"` |
| Session start or large context | `forgetforge status` |
| Prior fact needed | `forgetforge recall <topic>` |
| User says `#keep_forever` | `forgetforge keep <id>` |
| User says `#forget_this` | `forgetforge forget <id>` |
| Preprocessing or handoff brief received | `forgetforge import-brief --source manual --brief "<brief>"` |
| Diagnostics needed | `forgetforge doctor --json` |

Rules:

1. Read recall JSON before using a memory in your response.
2. If recall returns no matches, do not invent remembered facts.
3. If store returns `contradiction_warnings`, tell the user what conflicts and ask how to reconcile.
4. Hermes also exposes the same behavior through `forgetforge_*` tools and hot-context injection.
5. Slash commands available in Codex and Claude Code: `/forgetforge-recall`, `/forgetforge-status`, `/forgetforge-doctor`.
