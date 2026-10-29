# Personal fork ledger

Current differences from `herdrdev/herdr-app`; one row per maintained
capability, not per commit. Merge upstream verbatim, then check these seams.

| Capability | Owned files | Required upstream seams | Retirement condition |
| --- | --- | --- | --- |
| Fork maintenance | Top section of `AGENTS.md`, this file | None | Fork no longer maintained |
| Image lifetime | `vendor/gpui-kit/crates/shell/src/materialize/image.rs`; `src/mods/render_tests.rs` regression | `src/terminal.rs` release/eviction; shell `materialize.rs`, `materialize/components/avatar.rs` image sources; `plugin.rs` test access | Retire when upstream PR #25 merges with equivalent sharing and atlas retirement |

Personal agent recovery is developed under `local/agent-recovery/`. Record its
tested behavior and seams here when delivered; it requires no app Rust changes.
