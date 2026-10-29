# Personal fork ledger

Current differences from `herdrdev/herdr-app`; one row per maintained
capability, not per commit. Merge upstream verbatim, then check these seams.

| Capability | Owned files | Required upstream seams | Retirement condition |
| --- | --- | --- | --- |
| Fork maintenance | Top section of `AGENTS.md`, this file | None | Fork no longer maintained |
| Image lifetime | `vendor/gpui-kit/crates/shell/src/materialize/image.rs`; `src/mods/render_tests.rs` regression | `src/terminal.rs` release/eviction; shell `materialize.rs`, `materialize/components/avatar.rs` image sources; `plugin.rs` test access | Retire when upstream PR #25 merges with equivalent sharing and atlas retirement |
| Manual CI | Existing CI workflow | `.github/workflows/ci.yml`: `workflow_dispatch` trigger only | Upstream adds manual dispatch |
| Pinned-main Claude/Codex recovery | `local/agent-recovery/agent_recovery.py`, owning tests, README and `new-menu.patch` | Zero app Rust seams; personal new-menu local launch method/import context; existing terminal environment, `hc connect`, versioned docs; personal core `terminal.keep.recovery` | Remove when upstream offers explicit per-terminal pinned conversation recovery; in-process side/new/resume/fork never retarget a pin |
