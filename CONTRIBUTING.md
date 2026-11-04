# Contributing

People and coding agents follow the same rules. Agents: also read
[AGENTS.md](AGENTS.md), which explains the architecture in more detail.

## Before you push

```bash
scripts/check.sh              # daemon: lint, types, import boundaries, smoke run
apps/AgentPhone/check.sh      # app: SwiftLint, swift-format, Debug + Release build
```

CI runs the same scripts. A red check is not merged.

## Rules

- **Fix the code, not the linter.** Every Ruff rule is on, and ty treats warnings
  as errors. Suppress a rule only on a line where it is actually wrong, with the
  reason next to it: `# noqa: S311 - picks a phrase, not a secret`. Don't add
  ignores to `pyproject.toml` or `.swiftlint.yml` to get a change through.
- **No unit tests.** Prove a change by running the real code: add a scenario to
  `scripts/smoke.sh` or `scripts/scenarios/` that drives the fake adapters
  through the CLI and HTTP API. Bluetooth changes also need a run on a dongle.
- **Keep the layers apart.** `core/` holds the policy and the ports. Adapters talk
  through ports and events only, and don't import each other. `.importlinter`
  enforces this.
- **Every task has an owner.** Start background work through the owner's
  `core.tasks.Tasks`; its `stop()` or `close()` cancels the tasks and waits for
  them. Calls to `asyncio.create_task` are rejected by the linter.
- **No process-wide state.** One process runs one instance, but code must not
  depend on that. Put per-daemon state on `Context`.
- **Split files by responsibility before they reach 500 lines.** Don't split
  them into helpers that exist only to pass a complexity limit.
- **Comments say why, not what.** Keep comments about hardware and protocol
  quirks. Leave out comments that retell the diff ("moved from…", "for now").

## Changes people will notice

Add a line to [CHANGELOG.md](CHANGELOG.md) under the next version. Anything that
changes the HTTP API, events, CLI output or the config file goes under
**Breaking**, with what users need to change.

Commits use [Conventional Commits](https://www.conventionalcommits.org)
(`feat:`, `fix:`, `refactor:`, …). Pull requests are squash-merged.
