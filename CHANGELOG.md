# Changelog

Changes are recorded here using semantic versions. Until 1.0, review release notes carefully before upgrading an embedded application or stored database.

## [Unreleased]

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
