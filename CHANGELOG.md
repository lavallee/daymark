# Changelog

Changes are recorded here using semantic versions. Until 1.0, review release notes carefully before upgrading an embedded application or stored database.

## [Unreleased]

Development builds identify themselves as `0.6.0.dev0`. The tagged release
remains `0.5.0`.

### Upgrade and compatibility

- Running `daymark` with no arguments now prints a one-screen starting guide
  and exits successfully. `daymark --help` lists every command and global option.
- Local health, policy, export, and ordinary workspace reads return `no_database` for missing paths or SQLite files with no Daymark tables. Use `where` to check the selection and `init` to create a database.

- CLI snapshot import checks for readable JSON before opening the destination. A CSV or malformed JSON file returns `invalid` with a native-format hint.
- Concurrent first opens retry SQLite journal-mode contention before starting the task write.
- Existing database permissions survive repeated initialization. JSON exports publish after serialization and preserve an existing destination. An empty decision-maker argument now directs administrators to the explicit `policy --clear` command.
- A database with an unrecognized schema marker returns `conflict` before migration. Literal percent escapes in imported IDs round-trip on the oldest supported HTTP client.
- Make a native SQLite backup before upgrading. The schema marker stays at
  6. A writable open installs setting and policy revision tracking, including for later
  writes by a v0.5.0 process. A caller-owned connection must commit this
  installation. An existing read-only database can still serve ordinary
  record views. [Upgrade and rollback](docs/operations.md#upgrading-between-releases)
- Local administrators can use `policy --versioned` and pair `--if-version`
  with `--operation-id` to protect a reviewed policy change and recover its
  result. Legacy policy commands still return only the decider list.
- An explicit one-off local clock time must exist and identify one instant.
  Supply an offset when the clock repeats. An invalid `--tz` or `DAYMARK_TZ`
  now returns an error. Relative reminder retries retain their original instant
  across midnight and native transfer.
- A delayed `wait` or `resume` refuses finished or archived work. Accepting a
  proposal recorded with an outstanding wait places it in waiting. A new
  acceptance of an archived proposal requires explicit restoration.
- An undated recurrence starts on its first matching local day, including
  when an existing task joins a series. An explicit first date counts
  toward a finite schedule. Skip requires a visible, unfinished recurring
  task. Use `cancel` for one-off work and `reopen` to correct a closed round.
  Recorded operation results remain replayable. [Recurring work](docs/research/recurring-work.md)
- Older HTTP services can support ordinary reads while lacking newer scope
  filters, decision-policy evidence or verifiable retries. The current client
  checks support before using those features. [Mixed-version trials](docs/research/mcp-client-trials.md)

### Picking work and returning to it

- A waiting task shows whether resuming now would cancel its active reminders
  when it clears a future start. `wait` and `resume` accept the reviewed
  version or review token so a delayed reply cannot clear a different wait.
- `prime --brief` returns live counts, up to five ready tasks and remembered
  facts, the evaluation time and a portable activity checkpoint in one
  snapshot on a current store or service. `prime --scope NAME` limits startup
  to the requested area. MCP startup describes the native tools, and HTTP
  startup identifies the credential's workspace and actor.
- `agenda --deadlines --days N` shows agreed hard cutoffs, including work
  that is waiting or blocked. The ordinary text agenda separates earlier
  plans from work due or planned today.
- `agenda --local-day` groups timed hard cutoffs in the viewer's session
  timezone and lists overlapping date-only cutoffs in their originating
  zones. JSON retains both the recorded timing and viewer-local clock.
- `done` and `release` accept `--if-version N` (MCP `expected_version`).
  A returning tab can refuse a changed claim, including a renewal by the
  same actor. The check covers any closing or handoff note in the same write.
- Before skipping or completing a recurring round, `show ID --forecast skip|done` previews the current outcome and the next recorded dates. CLI, MCP and authenticated HTTP can read the same forecast.
- `ready --brief --json` returns the same compact queue rows as the structured MCP ready view. UIs can show the reason for each suggestion without loading its note history.
- `ready --explain` counts eligible and held tasks and gives examples for
  each reason. A brief shows the reviewed task's blockers, claim, attributed
  notes and source. It can identify a circular wait through a dependency
  and an unfinished child. A recurring brief names the round and date advanced.
- `ready --project ID` selects eligible project members before applying
  the limit. `take --project ID` claims an explicitly delegated member inside
  the same selection transaction. `projects --scope NAME` selects projects by their own scope
  and keeps their whole-project rollups.
- `find --page` supports cursor-based results and filters for scope, kind,
  status, active claims and archived work. `--source SYSTEM:KIND:ID` checks
  an exact external reference within the filters. `show --source REF`
  retrieves a known record, including archived records. Ordinary and paged
  text search share Unicode matching. Bounded ordinary searches load detail
  only for selected records. Plain listings keep note bodies off the wire on
  the current service.
- `daymark activity --after CHECKPOINT` follows changes and handoff notes.
  `show ID --since-version N` compares the current record with a reviewed
  version, including field excerpts and attributed note changes. Use
  `--note NOTE_ID` for the original note and `--before-note` for older notes.

### Preferences and consent

- Try an advice profile or `--set` and `--drop` adjustments for one request.
  Time-based choices use individual task estimates. Truncated advice reports
  ready tasks reviewed and available in `ready_coverage`. Project review finds
  open projects with no open or waiting task and distinguishes projects
  whose proposals still need a decision. Decision batching reports the
  reviewed population and labels the options offered in the final batch.
  Deadline lookahead flags planned work that falls after its hard cutoff,
  respecting each date's precision and timezone.
- Save and inspect named recipes with `profile --save-as NAME` and
  `profile --saved NAME`. Active settings and saved copies have guarded
  revisions. A local MCP adoption receipt includes an exact `restore_profile`
  call. An authenticated service can apply its own built-in stages and expose
  its profile catalog read-only. [Working preferences](docs/working-preferences.md)
- A proposal's detail reports whether the current actor may decide under
  workspace policy. Its connection can separately be read-only. A memory can
  have a scope and source. `forget` retires a reviewed memory. `unarchive`
  restores a record's visibility and preserves its lifecycle status.

### Working with other agents

- `take` atomically claims one ready task explicitly delegated to an agent.
  It accepts scope and context. An exact retry returns the original result,
  including an empty queue. `release --note` and `done --note` record the
  handoff or closing note together with the action.
- Ordinary writes verify the original intent on retry. Reviewed edits, undo,
  acceptance and dismissal can include an expected version and review token
  to detect intervening writes or ID reuse. An assessment and its evidence
  commit together. Restored history and recurring successors follow the
  original workspace event order after a sparse merge.
- Waiting on a new person or reply parks an open task immediately. Clearing
  the reason leaves the task waiting until explicit resumption. Follow-up
  advice honors its return time. MCP captures can assign a known person.
- MCP can schedule a reminder for existing work. Its receipt and brief name
  the time, recipient, channel and worker state. The brief explains when a
  proposal holds delivery. Archiving a reminder retains its recorded outcome
  and cancels an outstanding retry.

### Installing and embedding

- Print a read-only Claude Code or Codex launch configuration for the selected local workspace or authenticated HTTP service with `onboard --mcp-config`.

- SQLite backups contain the committed WAL data and can be read from a sealed,
  read-only folder as a single file. A writable Daymark open selects WAL
  again. A pre-existing backup destination is preserved.

- The wheel includes the matching agent skill, available through `onboard
  --skill`. The source distribution includes contributor guides and verification
  scripts. Python embedders can install the HTTP-only `[client]` extra and
  import `Client` and `ClientError` from `daymark`.
- `where --json` explains database selection without opening or upgrading
  the file. Project initialization protects the local database from Git and
  checks for conflicting locations. `onboard --check FILE` checks instructions
  without editing. Refresh preserves surrounding text and line endings.
- MCP negotiates supported protocol revisions, validates arguments before
  writing, and gives modern clients structured identifiers and versions.
  `mcp --read-only` exposes two review tools and opens local SQLite read-only.
  New CLI databases and their WAL files start private on POSIX. HTTP capture
  notes use the authenticated principal. Imported historical authors remain
  available. [Agent guide](docs/agents.md) · [MCP clients](docs/mcp.md)

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
