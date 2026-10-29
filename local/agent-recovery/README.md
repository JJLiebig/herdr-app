# Personal agent recovery

Optional Claude Code / Codex++ recovery in a Herdr terminal, with one persistent
JS menu seam and no app Rust changes. Runtime needs only this Python script.

## Setup

Requires Python 3 and `hc` on PATH, the personal core advertising scoped recovery,
`Terminal.resume_session` and `Recovery.session_token`, and the Codex++ build
emitting native OSC 7501 `session=` plus `sessiontoken=`. Stock Codex is not the
supported producer. Claude uses its native `--session-id` / `--resume` contract.

Copy `agent_recovery.py` to the app home at `local/agent-recovery/`. Create sibling
configs with direct argument arrays, for example:

```json
{"agent":"claude","argv":["C:/Users/me/.local/bin/claude.exe"]}
```

```json
{"agent":"codex","argv":["node","C:/Users/me/AppData/Roaming/npm/node_modules/@jjliebig/codex-plus-plus/bin/codex.js"]}
```

Keep normal vendor model/config/permission arguments; leave conversation selection
to the managed actions. User settings, plugins, environment and normal native
approvals stay in effect. The helper adds no capture hooks or daemon overrides.

In the personal app build, config presence opts that agent into local Windows
menu starts. Reopen the menu after changing configs. Clicks and menu number keys
share the launcher; current remote spaces, other platforms/agents and absent
configs retain their normal command. Global keys and `programs.json` are unchanged.

Manual launch inside the intended terminal:

```powershell
python C:/stable/agent-recovery/agent_recovery.py new --config C:/stable/claude.json
python C:/stable/agent-recovery/agent_recovery.py resume --config C:/stable/codex.json --session-id <uuid>
```

Both accept `--cwd <directory>` and otherwise use the shell's current directory.
Recovery keeps that launcher directory. Codex retains its native saved-directory
policy and may ask its usual directory question.

## Identity and limits

Codex++ reports its persistable main, not the displayed side conversation. Core
saves matching live root reports against the invocation UUID in the recovery
recipe and `HERDR_MAIN_INVOCATION`; old queued reports cannot bind a newer launch.
In-process New/Resume/Fork can replace the native main; `/side` cannot. Managed
New/Resume clear the prior binding. Recovery rotates the token without clearing
the durable native ID. Only a user-selected explicit Resume can fall back before
an initial native report; collected references never qualify.

Claude New chooses a UUID and saves it with the launch directory before spawning
`claude --session-id`. Recovery uses `--resume` with that exact UUID. Its pin stays
fixed for the managed invocation; use managed New/Resume to retarget it rather
than in-process New/Resume/Fork. No hook or transcript inspection is involved.

OSC is unauthenticated PTY output and has no persistence acknowledgement. An
unprocessed/unsaved report or a brand-new empty conversation is not recoverable
history. Both agents must retain history in their normal home on that host.
Native missing-history errors stay visible; there is no unrelated New fallback.

Setup/missing-identity/directory errors open a shell only after durable recovery
clear and lock release. If clearing fails, both errors are reported and the helper
exits. Every spawned vendor exit propagates its original status after cleanup.
A manual wrapper returns to its outer shell; an app-created terminal finishes.

## Rollback and checks

Remove or rename the opt-in configs and reopen the menu. Run `hc keep <terminal>
--off`, quit its managed agent, then remove the script. Inert helper documents may
remain; no vendor hooks, settings or trust-store edits need reversal.

```powershell
python -B -m unittest discover -s local/agent-recovery -p test_agent_recovery.py -v
```

Exact native qualification heads and receipts are separate verification artifacts.
Linux/macOS, actual OS reboot/power loss and paid model providers remain untested.
