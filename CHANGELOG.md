# Changelog

Changes are recorded here using semantic versions. Until 1.0, review release notes carefully before upgrading an embedded application or stored database.

## [Unreleased]

### Added

- Managed projects. A project can define its stages (`daymark project TITLE --stages SPEC`, `daymark stages PROJECT SPEC`); one that defines none reads as To do, Waiting and Done. A task has a `stage`, kept consistent with its status in both directions, a manual-order `rank`, and a `milestone` flag.
- `daymark move ID [--stage S] [--after X|--before X|--first|--last]`, `--stage` and `--milestone/--no-milestone` on `add` and `edit`, and the `place` engine action. `--assignee` also accepts a person's exact name.
- `daymark plan PROJECT` and `daymark projects`; `Store.plan` and `Store.projects` read a project in one transaction with roll-ups and the head event cursor.
- The project change feed: `Store.head`, `Store.changes`, `events(project=)`, `daymark changes PROJECT --after N`, `daymark events --head` and `--project`.
- `Store.batch` and `POST /v1/operations/batch`: several operations in one transaction, all-or-nothing or keeping the successes, each with its own operation ID and expected version.
- HTTP `GET /v1/projects`, `/v1/projects/{id}/plan`, `/v1/projects/{id}/changes`, `/v1/events/head`, and `?project=` on `/v1/events`, with matching client methods. MCP gains the `projects` and `plan` views and a `move` action.
- `managed_by` on a project marks one another system owns; plan editors treat it as read-only.

### Changed

- The stored schema upgrades in place on first open (schema 5): every entity gains `stages`, `stage`, `rank`, `milestone` and `managed_by` with their defaults. Versions, events and receipts are untouched, and entities that use none of the new fields read, rank and render as before. Snapshots exported afterwards contain fields that 0.3.0 does not accept; back up before upgrading.
- `--assignee` with a value that is neither a person's ID nor a person's name is refused with `invalid` and the fix, before the write (previously the engine refused the missing reference).

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
