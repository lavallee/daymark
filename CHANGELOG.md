# Changelog

Changes are recorded here using semantic versions. Until 1.0, review release notes carefully before upgrading an embedded application or stored database.

## [Unreleased]

- Schema upgrade notices give the rollback path. Older releases differ in whether they refuse an upgraded file.

- `mcp --read-only` offers a two-tool review session, opens local SQLite read-only and restricts an HTTP-backed session to read requests.

- Editing an open task’s waiting reason parks it immediately and removes it from ready and take. Clearing a reason keeps the task waiting until an explicit resume.
- A repeating task’s brief identifies its round, rule, time zone and the date advanced for a generated occurrence.
- `show ID --since-version N` compares current terms with the version a person reviewed, including field excerpts and attributed note changes. Exact source references also work. Missing and ambiguous historical snapshots are reported explicitly. `edit`, `accept` and `dismiss` also accept `--if-review TOKEN` from the original show or brief to detect an ID that was deleted and reused.

- HTTP clients can read, edit and replay imported entity and operation IDs containing slashes or URL path punctuation. Query-based forms support exact lookup behind URL-decoding gateways.
- Use `--context ID` with `ready`, `agenda` or `advise` to select tasks tagged with an existing context. The filter combines with scope and honors every dependency.

- `show --source SYSTEM:KIND:ID` recovers a capture by exact source reference in the active workspace, including archived entities. It also supports brief and individual note reads.

- `ready --explain` counts eligible and held tasks within the selected filters and names example tasks behind each reason.

- Accepting a proposal with `--waiting-on` now places it in waiting. `accept` and `dismiss` support `--if-version N` for a reviewed proposal. Both full and brief task details show source references.

- `where [--json]` explains local file selection and workspace identity without opening or upgrading a database. For HTTP it reports the service origin.
- `find --page` returns compact, cursor-based search hits. Filter by kind, status and scope and continue with `--after ID`. `find --claimed` recovers the current worker’s live claims, including held work. Brief startup reads cap task and memory previews.

- `show ID --brief` provides a bounded handoff with attributed note previews, lease and readiness. `--note NOTE_ID` reads the entire note and `--before-note NOTE_ID` pages back through earlier notes.

- Plain-verb retries verify the original write against its existing receipt, including
  the entity, action and translated fields. `edit --if-version N` lets a caller reject a
  stale decision. An assessment and its evidence note now commit together.

- Project initialization adds a database-specific Git ignore file when one is absent. It refuses a conflicting database location with `--here`. `onboard --check FILE` checks project instructions without editing them. Refreshes preserve surrounding content and line endings.
- Task details explain every applicable readiness constraint from the current actor’s perspective.

- Try profile adjustments for one call with `advise --set` or `--drop`. The
  optional `time-fit` strategy offers tasks individually estimated to fit a
  stated window. Save a reusable configuration with `profile --save-as NAME`,
  inspect or export it with `profile --saved NAME`, and choose it when needed
  with `advise --profile NAME`. Local preference writes accept a retry operation ID.
  Active profiles and saved copies have versions for safe updates. Active changes
  return a command that restores the previous choice at the observed revision.
  Plain and gentle advice only fetch the ready queue for ordinary-sized lists.

- `daymark --as WORKER take [--scope NAME] [--context ID]` atomically claims one ready task marked for an agent. Repeating the same operation returns the original task or empty result. The engine, CLI and MCP expose the same selection.

- MCP negotiates supported protocol revisions, distinguishes request errors from tool refusals,
  validates arguments before writing, and accepts replay keys on every write. Modern clients
  receive a machine-readable ID and version with each successful write.

- `daymark activity [--after CURSOR]` reads recent edits and handoff notes in a compact page. Available through Python, HTTP and MCP.

- `prime` shows the active session and live work first. Use `--brief` to skip the guide. Structured output includes session context. The ready count includes the actor’s claims and covers workspaces larger than 1,000 ready tasks.

- `release ID --note TEXT` hands unfinished work back with an attributed note. `done ID --note TEXT` now commits the closing note and completion in a single transaction. `show ID` displays the expiry of an active claim.

## [0.5.0] - 2026-11-15

### Upgrading

- The schema upgrade to 6 is one-way, as in 0.4.0: back up with the version you run now (`daymark backup --file BEFORE.sqlite3`) before installing 0.5.0. Entities that use none of the new fields read as before.

### Added

- **The strategy layer** (`daymark.strategies`): named, parameterised ways of working that read the kernel's public views and return advice, never a write. Nine built-ins (`shortlist`, `first-step`, `stale`, `waiting-check`, `overbooked`, `lookahead`, `decisions-first`, `frontier-first`, `neutral-aging`), each labelled with how well its idea is supported.
- **Profiles**: `plain` (the default, which changes nothing), `assistant`, `gentle` and `dense`. `daymark profile [NAME] [--set STRATEGY.PARAM=VALUE] [--drop STRATEGY]`, `daymark advise [--profile NAME]`, `daymark strategies`, and an `advise` view in MCP. A profile belongs to a workspace.
- **Plugins**: a package offers strategies under the `daymark.strategies` entry-point group; one is imported only when a profile names it.
- **Conditions**: a `condition` kind for what must be true for a goal to be reached, with a state (met, partly met, unmet, unknown) and evidence; `advances` on tasks; tasks and conditions may wait on conditions. `daymark condition`, `daymark assess`, `daymark explore [GOAL]`, `--advances`, `Store.explore`, `GET /v1/explore`, an `explore` view in MCP.
- `Store.setting` and `Store.set_setting`: small per-workspace configuration values.
- [docs/architecture.md](docs/architecture.md) and [docs/research/working-methods.md](docs/research/working-methods.md).

### Changed

- `depends_on` accepts conditions as well as tasks. A condition's status follows its state and cannot be set directly; it belongs to a goal and cannot be proposed, parked or made to repeat.
- MCP: an `assess` action with `state`.

## [0.4.0] - 2026-11-13

### Upgrading: read this first

- **The schema upgrade is one-way.** The first time 0.4.0 opens a database it rewrites every stored entity in place (schema 5). Daymark 0.3.0 cannot open the file afterwards: it fails cleanly (`invalid`, unknown fields), and any long-running 0.3.0 process using the file stops working at that moment. Snapshots exported afterwards are refused by 0.3.0 too.
- **Back up with 0.3.0, before installing 0.4.0**: `daymark backup --file BEFORE.sqlite3`. Every 0.4.0 command line verb (including `ready`, `serve`, `mcp` and `worker`) upgrades the file it opens, and says so once on standard error (`daymark: upgraded FILE from schema 4 to 5 (Daymark 0.4.0); older versions can no longer open it`). `daymark backup` copies without opening a Store, so it never upgrades. Rolling back means restoring the backup.
- Versions, events and receipts are untouched, and entities that use none of the new fields read, sort and render as before.
- A database written by a newer Daymark is now refused (`conflict`) instead of being rewritten. The library's reads (`plan`, `projects`, `changes`, `goals`, `tree`, `export`, `get`, `list`, `query`, `ready`, `agenda`) on a schema a caller has not upgraded are refused with one `conflict` message rather than upgrading as a side effect or answering in the old shape; `Store(path, upgrade=False)` refuses an old schema without writing; a read-only connection on an old schema gets `conflict` instead of a database error.

### Added

- Projects, each piece optional: stages kept consistent with status in both directions; a manual order (`daymark move ID --after X`, the `place` action); item types and hierarchy roll-ups (`--type`, `--parent`, `daymark tree`); milestones as targets (`--is-milestone`, `--milestone M`) with roll-ups; typed custom fields per project (`--set FIELD=VALUE`, filters on `values.ID`, totals in roll-ups); project facts (lead, dates, goal) and health counts; dependency date conflicts and structural warnings as facts.
- `daymark project add|set|stages|types|fields|config|presets`, five presets (simple, kanban, milestones, epics, event) and a configuration document that applies in one write.
- Goals: a target date, an optional hand-kept measure, and `daymark goal add|set|show`, `daymark goals`, `Store.goal_plan`, `Store.goals`, `GET /v1/goals` and `/v1/goals/{id}/plan`.
- `daymark plan PROJECT [--by milestone|type|parent|assignee|FIELD] [--tree]` and `daymark projects`; `Store.plan`, `Store.projects` and `Store.tree` read in one transaction with the head event cursor; `GET /v1/projects`, `/v1/projects/{id}/plan`, `/v1/tree/{id}`.
- The project change feed: `Store.head`, `Store.changes`, `events(project=)`, `daymark changes PROJECT --after N`, `daymark events --head` and `--project`, `GET /v1/events/head`, `/v1/projects/{id}/changes` and `?project=` on `/v1/events`.
- `Store.batch` and `POST /v1/operations/batch`: several operations in one transaction, all-or-nothing or keeping the successes, with an optional request ID that makes the batch idempotent as a unit.
- `daymark.plans.schedule` and `Tasks.schedule`: write a plan's start, end and deadline back to Daymark's fields, keeping precision and never moving a deadline as a side effect.
- `managed_by` on a project marks one another system owns.
- MCP: `projects`, `plan` (with `by`), `goals` and `goal` views; `type`, `milestone`, `is_milestone` and `values` on add and edit; a `move` action.

### Changed

- `--assignee` with a value that is neither a person's ID nor a person's name is refused with `invalid` and the fix, before the write.
- An engine `update` carrying `values` merges per field (null clears one) on every path: library, batch and HTTP.
- A `create` may not set `created_at`: it orders tasks never placed. History is restored through snapshot import.
- Custom number values are finite, and whole numbers are kept exact within ±(2**53 − 1); anything else is refused with `invalid`.
- A request that is not plain JSON (NaN, infinity) is refused with `invalid`.

## [0.3.0] - 2026-11-10

### Added

- `focus`: the person's own ordered short list (`daymark focus ID [--rank N]`, `unfocus`). Focused tasks lead `ready`.
- `scope`: a filter within a workspace (`--scope NAME` on `add`, `edit`, `ready` and `agenda`; `--unscoped`), on the HTTP views and as a filter field.
- `daymark workspaces` and `Store.workspaces()`; `daymark prime` names the database and workspace it shows.

### Changed

- The stored schema upgrades in place on first open (schema 4). Snapshots exported afterwards contain fields that 0.2.0 does not accept.
- Completing, cancelling or archiving a task clears its focus.

## [0.2.0] - 2026-11-09

### Added

- Plain verbs for agents and people, on the command line and as `daymark.Tasks`: `add`, `propose`, `ready`, `agenda`, `show`, `claim`, `release`, `note`, `done`, `skip`, `wait`, `resume`, `defer`, `edit`, `block`, `unblock`, `accept`, `dismiss`, `remind`, `remember`, `find`. They mint short IDs, read versions themselves, and accept a small fixed date and recurrence grammar.
- `daymark init`, `daymark prime` and `daymark onboard` for setting up and orienting an agent session; `daymark mcp`, a dependency-free MCP server over stdio.
- Readiness and agenda views (`Store.ready`, `Store.agenda`, `Store.context`, `Store.summary`) and matching HTTP routes and client methods.
- Working claims with a lease, append-only notes, a `proposed` status with accept and dismiss, `next_move`, `waiting_on`, `estimate_minutes`, a `memory` kind, and an `expression` on temporal values that keeps the phrase a date came from.
- Compact text and `--json` output; coded errors with distinct exit statuses.
- Every event records the actor that caused it. A workspace policy (`daymark policy --deciders`, `Store.set_policy`) limits who may accept or dismiss proposals; the HTTP service enforces it against the authenticated principal and returns 403.

### Changed

- Writes validate only their own references through derived lookup tables. On a synthetic 10,000-entity workspace a create went from about 215 ms to about 6 ms on the development machine.
- Path-owned stores use SQLite WAL mode.
- The stored schema upgrades in place on first open. Snapshots exported afterwards contain fields that 0.1.0 does not accept; back up before upgrading.
- Rounds of a repeating task are named `series.N`. Priority is defined: 1 is most important, 0 unset.
- The `--db` default is now discovered (`DAYMARK_DB`, nearest `.daymark/`, `./daymark.sqlite3`, per-user database). Domain failures on the command line print their code and exit 3, 4 or 5; they previously printed a generic message and exited 1.

## [0.1.0] - 2026-11-09

### Added

- Typed tasks, projects, goals, people, principals, labels, contexts, filters, and reminders, with explicit lifecycle and structured relationships.
- Separate hard deadlines, planned times, start/target windows, and local-date or timezone-aware temporal values. Bounded fixed-anchor and completion-based recurrence.
- SQLite storage with caller-owned transaction support, expected-version checks, operation replay receipts, event history, and native snapshot import/export. Task deletion cancels and detaches reminders while preserving their original links in history; other inbound references reject atomically. skip advances recurrence without recording completion.
- Optional authenticated HTTP API and Python client, local/remote CLI, and backup/restore commands.
- A recording worker with durable leases, per-claim fencing, retries, and stable delivery keys. The default sink sends no external notifications; custom receivers need persistent deduplication and a qualified transaction design.
- MIT license, synthetic examples, contributor and security guidance, CI, and a release checklist.
