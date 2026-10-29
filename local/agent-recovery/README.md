# Personal agent recovery

Opt-in launcher for Claude Code and Codex in an **existing** Herdr terminal.
No app Rust changes, global vendor hooks, daemon, transcript search, or vendor
patches. Requires Python 3 and `hc` on PATH, and the personal herdr-core patch
supporting `terminal.keep.recovery` with `expected_scope`.

The managed launcher claims the terminal with a fresh invocation token, registers
one stable recovery command durably, then starts the native CLI in the same PTY.
A native SessionStart hook pins its main UUID and directory using versioned core
documents. A nonblocking OS file lock, keyed by exact core endpoint and terminal,
prevents two launchers sharing one PTY; hooks remain lock-free. OS process loss
releases the lock, and cleanup finishes before another launcher can claim it. The hook is silent on success and never updates status or the recovery
command. Existing OSC 7501 status hooks continue to own status.

## Setup

Keep this directory at a stable absolute path on **each host** running agents.
Create a separate personal JSON config per vendor, for example:

```json
{"agent":"claude","argv":["C:/Users/me/.local/bin/claude.exe","--model","sonnet"]}
```

```json
{"agent":"codex","argv":["/absolute/path/codex","--model","my-model"]}
```

`argv` is a direct argument array, without shell escaping. On Windows use the
actual vendor executable, not npm's `.ps1`/`.cmd` shim. Preserve your normal
model, permission, and config flags here. Use only interactive CLI options;
conversation selection belongs to the managed New/Resume actions below. Do not
put prompts or secrets in this file. The core stores only this config's path,
the helper command, invocation identity, UUID, and working directory.

Inside the intended terminal:

```powershell
python C:/stable/agent-recovery/agent_recovery.py new --config C:/stable/claude.json
python C:/stable/agent-recovery/agent_recovery.py resume --config C:/stable/codex.json --session-id <uuid>
```

`new` also accepts `--cwd <directory>`. Explicit Resume uses that directory (or
the shell's current directory). The CLI must find its saved session in its normal
agent home; this helper does not move histories or credentials between hosts.

For the local Windows menu, copy this bundle under the app home at
`local/agent-recovery/` and create sibling `claude.json` and `codex.json` configs.
The Codex config may also use `["node", "C:/Users/me/AppData/Roaming/npm/node_modules/@jjliebig/codex-plus-plus/bin/codex.js", ...]`.
The tracked `new-menu.patch` wraps only local Windows Claude/Codex starts, for
both clicks and the menu's number shortcuts. Other agents and remote spaces
keep their existing commands. Apply it from your app home, after inspecting
any personal edits to `mods/new-menu/main.js`:

```powershell
git -C "$env:USERPROFILE/.config/herdr-app" apply --check C:/stable/agent-recovery/new-menu.patch
git -C "$env:USERPROFILE/.config/herdr-app" apply C:/stable/agent-recovery/new-menu.patch
```

The mod reloads when saved. To revert, use the same command with `apply -R`.
Do not globally override `programs.json` with Windows paths: they also affect
remote hosts. Provision the helper and config on a remote host before enabling
managed launches there. No global keyboard bindings are changed.

Accept the vendor's **normal native hook approval** for this concrete helper.
No trust entries or permission bypass flags are supplied. Declining or disabling
hooks means no usable capture and no recovery promise. Codex is launched with
`--no-daemon`: the qualified local CLI owns its hook configuration and environment.
Remote/shared-server variants are not qualified. Claude uses invocation-local
`--settings`; Codex uses invocation-local `-c hooks.SessionStart`. Independently
started CLIs do not receive those options merely by inheriting the environment.

## Pinned-main limit

Claude New preallocates its expected UUID. Codex New accepts only root `startup`
SessionStart; resumed, forked, cleared, compacted and subagent events cannot
initialize its pin. Codex emits startup at the first turn, so merely opening the
CLI does not capture a recoverable session. There is no promise before capture.

Once captured, the main UUID never changes during that managed invocation.
`/side` works and remains ephemeral; recovery returns to the pinned main. Native
in-process `/new`, `/resume`, and `/fork` do not retarget recovery. Use managed
New or explicit Resume to select a different persistent main. SessionEnd never
clears the pin. Delayed hooks from old launches cannot replace the current owner.

On core/holder recovery the core runs the stable helper, which resumes the exact
pin in its saved directory. A known resume UUID remains durable across invocation
rotation, including before a new hook. Missing capture, unavailable directory,
invalid UUID, or setup/registration failure produces an error and, after a durable
recovery clear, an interactive shell (PowerShell 7 when available on Windows).
The invocation lock releases before that shell, so managed New/Resume can run
there. If the core cannot acknowledge clearing recovery, the helper reports both
the original and cleanup error and exits without creating a rearmed shell.

Every actually spawned vendor CLI exit propagates its original status after
clearing recovery, including a nonzero exit or native Resume failure. Native
errors stay visible. A manual launch returns to its outer shell; an app-created
terminal finishes. The helper does not classify vendor errors or silently open
an unrelated new conversation. Later core loss cannot retry the cleared recipe.

To roll back: disable the personal local launch-menu integration, and run
`hc keep <terminal> --off` for each managed terminal before removing the helper.
This clears its registered recovery command. Leave the bundle in place until
those managed agents have exited. The namespaced `herdr-main-t*` core
documents can remain inert; they contain no transcripts or environment secrets.
No global vendor hook was installed. Any normal native approval granted for this helper can remain inert.

## Verification

```powershell
python -B -m unittest discover -s local/agent-recovery -p test_agent_recovery.py -v
```

Tests own helper policy at the core document boundary. Native qualification on
Windows: Claude Code 2.1.312 root startup with explicit UUID and invocation-only
settings, without any prompt; stock Codex 0.164.0 and Codex++ 0.165.0-fork.1 root startup from an initial
turn against a dead loopback-only synthetic provider. Codex normal hook approval
was exercised through its isolated native PTY. No credentials were copied and no
paid model calls were made. Unix/macOS and remote/shared Codex servers remain
unqualified. Native stock Codex same-ID resume after literal core stop/start and exact
core+holder process loss was verified with the personal core build. Planned
restart adopted the surviving invocation; ordinary exit cleared keep and stayed
finished across a later restart. Scratch baseline SessionStart hooks and
invocation hooks both fired for Claude and Codex++; a second independent CLI inherited the
managed environment but only the baseline hook, proving invocation config stays
local. The personal menu patch applied cleanly and passed `herdr-app check` in
a scratch app home. Claude native resume was also verified with a synthetic local history fixture
and an exact SessionStart:resume UUID, without any model requests. End-to-end
core-loss recovery with Claude remains unverified.
