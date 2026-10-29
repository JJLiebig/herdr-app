# Personal agent recovery

Optional Claude Code / Codex++ recovery in a Herdr terminal, with one persistent
JS menu seam and no app Rust changes. Runtime needs only this Python script. It attaches directly to the terminal core
endpoint and never starts a stopped core.

## Setup

Requires Python 3.11+ and the personal core advertising scoped recovery,
`Terminal.resume_session`, and the Codex++ build emitting native OSC 7501
`session=`. Stock Codex is not the supported producer. Claude uses its native
`--session-id` / `--resume` contract.

Copy `agent_recovery.py` to the app home at `local/agent-recovery/`. Create sibling
configs with direct argument arrays, for example:

```json
{"agent":"claude","argv":["C:/Users/me/.local/bin/claude.exe"]}
```

```json
{"agent":"codex","argv":["node","C:/Users/me/AppData/Roaming/npm/node_modules/@jjliebig/codex-plus-plus/bin/codex.js"]}
```

Keep normal vendor model/config/permission arguments; leave conversation selection
to the managed actions; do not store starting prompts or inline secrets here.
User settings, plugins, environment and normal native approvals stay in effect.
Claude's two identity hooks below register plain shell launches too. Status stays
native OSC 7501; the hooks only register recovery and clear deliberate exits.

### Claude: plain shell launches

Add these command hooks to Claude's user `settings.json`, preserving existing
hooks. Replace the example paths with the installed helper/config and use your
Python 3.11+ interpreter. The 20-second hook budget covers the short hook lock,
the helper's five-second IPC deadline and connection close.

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python C:/stable/agent-recovery/agent_recovery.py claude-hook --config C:/stable/claude.json", "timeout": 20}]}],
    "SessionEnd": [{"matcher": "prompt_input_exit|logout", "hooks": [{"type": "command", "command": "python C:/stable/agent-recovery/agent_recovery.py claude-hook --config C:/stable/claude.json", "timeout": 20}]}]
  }
}
```

New attended `claude` launches in Herdr enroll automatically, including launches
through the menu. `SessionStart` supplies the actual native UUID on startup,
resume, clear and foreground branch. `CLAUDE_PID` owns cleanup; a delayed exit
from a different process cannot clear the current session. Background jobs,
headless runs and subagents are ignored. `SessionEnd` clears only deliberate
exit/logout; clear/resume handoffs, signal exits and a stopped core preserve the
saved choice. Recovery runs the configured Claude argv with `--resume` directly,
so core observes its native exit/signal lifecycle. Exiting Claude returns to the
existing shell.

The attendance gate uses Claude's engine-provided
`CLAUDE_CODE_SESSION_ATTENDED=1`, validated with Claude Code 2.1.312. That flag is
not a documented public API; missing/changed metadata leaves recovery inactive.
`--bare`, safe mode and disabled hooks also leave plain launches unenrolled.
Install the hooks on the host where Claude runs; viewer platform does not matter.

In the personal app build, config presence opts that agent into local Windows
menu starts. Reopen the menu after changing configs. Clicks and menu number keys
share the launcher; current remote spaces, other platforms/agents and absent
configs retain their normal command. Global keys and `programs.json` are unchanged.
An opted-in local entry can launch its configured executable even when the stock
command is absent from PATH.

Manual launch inside the intended terminal:

```powershell
python C:/stable/agent-recovery/agent_recovery.py new --config C:/stable/claude.json
python C:/stable/agent-recovery/agent_recovery.py resume --config C:/stable/codex.json --session-id <uuid>
```

Both accept `--cwd <directory>` and otherwise use the shell's current directory.
Recovery keeps that launcher directory. Codex retains its native saved-directory
policy and may ask its usual directory question.
Recovery uses the core's environment, not later shell/profile changes. Set a
required native home or PATH on that core (or with `hc run --env NAME=value`);
use supported explicit vendor arguments for config selection. No environment
snapshot or secret map is stored by the helper.

## Identity and limits

Codex++ reports its persistable main, not the displayed side conversation. Core
saves the latest reported main session from live root reports while recovery is
registered. In-process New/Resume/Fork can replace the native main; `/side`
cannot. Managed New/Resume clear the prior binding. Recovery preserves
the durable native ID. Only a user-selected explicit Resume can fall back before
an initial native report; collected references never qualify.

The explicit Claude launcher can still choose a UUID with `--session-id`.
Identity hooks replace that pin with Claude's actual attended main UUID and cwd,
including in-process session changes. Recovery uses `--resume` with the saved
UUID. No transcript inspection is involved.

OSC is unauthenticated PTY output and has no persistence acknowledgement. An
unprocessed/unsaved report or a brand-new empty conversation is not recoverable
history. Both agents must retain history in their normal home on that host.
Native missing-history errors stay visible; there is no unrelated New fallback.

Setup/missing-identity/directory errors open a shell only after durable recovery
clear and lock release. If clearing fails, both errors are reported and the helper
exits. Every spawned vendor exit propagates its original status after cleanup.
Existing outer shells stay available, including the app's normal menu shell.
Finality means recovery is cleared: a later core start will not resume the agent.
Direct helper terminals finish. Cleanup failure is reported; a nonzero vendor
status is retained, otherwise the helper returns an error.

## Rollback and checks

Remove these two recovery hook commands from Claude's user settings and remove
or rename the opt-in configs, then reopen the menu. Run `hc keep <terminal>
--off`, quit its managed agent, then remove the script. Inert helper documents may
remain. Other hooks/settings need no changes.

```powershell
python -B -m unittest discover -s local/agent-recovery -p test_agent_recovery.py -v
```

Exact native qualification heads and receipts are separate verification artifacts.
Linux/macOS, actual OS reboot/power loss and paid model providers remain untested.
