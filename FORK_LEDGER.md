# Personal fork ledger

Current differences from `herdrdev/herdr-app`; one row per maintained
capability, not per commit. Merge upstream verbatim, then check these seams.

| Capability | Owned files | Required upstream seams | Retirement condition |
| --- | --- | --- | --- |
| Fork maintenance | Top section of `AGENTS.md`, this file | None | Fork no longer maintained |
| Manual CI | Existing CI workflow | `.github/workflows/ci.yml`: `workflow_dispatch` trigger only | Upstream adds manual dispatch |

Personal agent recovery is developed under `local/agent-recovery/`. Record its
tested behavior and seams here when delivered; it requires no app Rust changes.
