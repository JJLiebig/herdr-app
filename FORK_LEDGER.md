# Personal fork ledger

Current differences from `herdrdev/herdr-app`; one row per maintained
capability, not per commit. Merge upstream verbatim, then check these seams.

| Capability | Owned files | Required upstream seams | Retirement condition |
| --- | --- | --- | --- |
| Fork maintenance | Top section of `AGENTS.md`, this file | None | Fork no longer maintained |
| Image lifetime | `vendor/gpui-kit/crates/shell/src/materialize/image.rs`; `src/mods/render_tests.rs` regression | `src/terminal.rs` release/eviction; `src/window.rs` asset invalidation; shell `lib.rs` invalidation export, `materialize.rs`, `materialize/components/avatar.rs` image sources; `plugin.rs` test access | Retire when upstream PR #25 merges (or upstream ships equivalent) |
| Manual CI | Existing CI workflow | `.github/workflows/ci.yml`: `workflow_dispatch` trigger only | Upstream adds manual dispatch |
| Native-main Codex++ and Claude recovery | `local/agent-recovery/`, `defaults/mods/new-menu/main.js`, `defaults/AGENTS.md` | Zero app Rust seams; one persistent JS menu seam; Claude user SessionStart/CwdChanged/SessionEnd hooks (native UUID, owner PID, attended-session flag; latest transcript cwd under hook lock); existing terminal environment, attach-only RPC and versioned docs; personal core scoped `terminal.keep.recovery` and `Terminal.resume_session`; native Codex++ OSC identity | Remove when upstream supplies per-terminal native main-conversation recovery; Codex++ excludes side views; Claude follows attended main session and directory changes, including plain shell launches |
| Palette icon per platform (upstream PR #478) | `defaults/frame/palette/main.js` input icon; `scripts/palette.test.ts` platform test | None beyond the owned lines | Retire when upstream PR #478 merges (or upstream ships equivalent) |
| Change anything | `src/window/ask.rs`, `defaults/frame/ask/`, `defaults/mods/workspace-chrome/message-circle.svg` | `src/window.rs`: `mod ask`, startup `forget_ask`, `app.ask` arm, `watch_ask` in `tend_terminals`; `src/commands.rs` entry and generated `docs/commands.md`; workspace-chrome footer row and agent-icon hold; palette entry; `defaults/AGENTS.md` command line | Upstream ships it, or Can declines and the fork drops it |
