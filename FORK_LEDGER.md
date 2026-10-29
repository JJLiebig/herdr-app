# Personal fork ledger

Current differences from `herdrdev/herdr-app`; one row per maintained
capability, not per commit. Merge upstream verbatim, then check these seams.

| Capability | Owned files | Required upstream seams | Retirement condition |
| --- | --- | --- | --- |
| Fork maintenance | Top section of `AGENTS.md`, this file | None | Fork no longer maintained |
| Image lifetime | `vendor/gpui-kit/crates/shell/src/materialize/image.rs`; `src/mods/render_tests.rs` regression | `src/terminal.rs` release/eviction; shell `materialize.rs`, `materialize/components/avatar.rs` image sources; `plugin.rs` test access | Retire when upstream PR #25 merges with equivalent sharing and atlas retirement |
| Manual CI | Existing CI workflow | `.github/workflows/ci.yml`: `workflow_dispatch` trigger only | Upstream adds manual dispatch |
| Native-main Codex++ and Claude recovery | `local/agent-recovery/`, `defaults/mods/new-menu/main.js`, `defaults/AGENTS.md` | Zero app Rust seams; one persistent JS menu seam; Claude user SessionStart/SessionEnd identity hooks (native UUID, owner PID, attended-session flag); existing terminal environment, attach-only RPC and versioned docs; personal core scoped `terminal.keep.recovery` and `Terminal.resume_session`; native Codex++ OSC identity | Remove when upstream supplies per-terminal native main-conversation recovery; Codex++ excludes side views; Claude follows attended main session changes, including plain shell launches |
