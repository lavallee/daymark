# Decisions for the first release

Decision date: 2026-11-09. The implementation owner chose these defaults under the
commissioner's delegated routine-decision authority. This records design choices;
it does not assert that an unfinished implementation has passed acceptance. The
[comparison ADRs](adr-comparison.md) retain the evidence and alternatives. Release
readiness remains governed by [RELEASING.md](../RELEASING.md).

| Research decision | Disposition for v1 | Contract and limit |
|---|---|---|
| C01 Authority independent of organization | Adopt distinction; defer team-role system | Workspace is the authorization scope. Projects, goals, labels, assignment and saved views grant no access. The standalone service maps server-configured bearer identities to one workspace; applications own membership and provisioning. No invitations or per-task sharing. |
| C02 Optional typed structure | Adopt | Independent task, project, goal, person, principal, label, context, filter and reminder kinds. One decomposition parent, separate project/goal associations, lightweight checklists. No automatic child-to-parent completion or multi-parent ownership. |
| C03 Separate temporal meanings | Adopt; structured period labels deferred | Deadline, planned, earliest start and latest target stay independent. A preferred evening can be retained as explicit preference/provenance without manufacturing a clock instant. No automatic interpretation of natural language. |
| C04 Date precision and timezone | Adopt date and zoned instant; defer floating local-clock type | Each temporal value is either a civil date plus governing IANA zone, or an aware instant plus zone. All-day lateness changes after the local day. The API rejects naive instants. A future floating-time type needs explicit migration semantics. |
| C05 Recurrence basis and history | Adopt | Fixed-anchor and after-completion recurrence differ. Materialized successors retain a durable series/occurrence identity and actual occurrence index. Replaying a completion must not spawn twice. User activity is never inferred from passage of time. |
| C06 Missed slots and calendar arithmetic | Adopt explicit bounded subset | Calendar rules support daily, weekly, monthly and yearly frequencies; subday expansion is rejected. DST gaps move forward; overlaps use first fold. Search limits raise an explicit error rather than pretend a series ended. Complex scheduling engines and automatic dependency date shifts are deferred. |
| C07 Reminder intent/firing/delivery | Adopt | Reminder state is separate from task timing and completion. Durable retry, leases, per-claim fencing and stable delivery keys support recovery. Default recording creates no external notification. A receiver must deduplicate for external side effects; exactly-once delivery is not promised. |
| C08 Dependencies separate from hierarchy | Adopt | Explicit dependency edges with workspace/kind checks and cycle rejection. No critical-path planner, automatic rescheduling, effort estimation or resource leveling. |
| C09 Saved views as authorized queries | Adopt | Bounded filter AST evaluates only authorized workspace records. No raw SQL, executable expressions, or view-granted permissions. Query semantics distinguish hard overdue from soft planned work. |
| C10 Offline/conflict contract | Adopt conflict safety; defer replicated offline editing | Stable operation receipts and expected versions make replay and stale-write outcomes explicit. SQLite is the local authority. No CRDT, device sync or automatic collaborative merge claim. |
| C11 Canonical export and loss reporting | Adopt canonical snapshots; defer third-party interchange | Native export/import preserves identities, versions, event history and operation replay receipts. Imports validate references atomically. Any application-specific downgrade must report/refuse semantic loss. No CalDAV/CSV/vendor round-trip guarantee. |
| C12 Extensibility and adapters | Adopt | Typed core fields plus structured metadata and source references. Application adapters and delivery sinks are separate; core does not import private producers. No plug-in execution from untrusted imports. |
| C13 Privacy/licensing | Adopt | MIT standalone source, synthetic examples, protected local state, redacted HTTP/worker errors. Source, dependencies and archives require privacy/security review. A successful secret scan does not establish absence of private data. |
| C14 Personal overlays | Defer | Planning fields currently belong to the shared record. Private per-person planning overlays need separate visibility/version semantics. |
| C15 Goals without mandatory methodology | Adopt basic kind; defer computed metrics | Goals and projects are optional explicit records; assignee and beneficiary differ. No mandatory household, GTD, sprint or OKR workflow. No implicit goal-progress formula. |
| C16 Standalone execution/deployment | Adopt | Embedded SQLite first, optional authenticated HTTP service/SDK/CLI. Supplied connections preserve the caller's unit of work; path-owned connections manage their own commits. Durable scheduler and restore drills are operational acceptance, separate from package build success. |

## Decisions for the agent surface

Decision date: 2026-11-09. These defaults were chosen by the implementing agent
under the commissioner's delegated routine-decision authority and are open to
reversal. [Work and life](work-and-life.md) gives the reasoning.

| Decision | Default chosen | Contract and limit |
|---|---|---|
| A01 One readiness rule | Adopt | Ready means open, unarchived, start date arrived, all dependencies completed or cancelled, no unfinished child task, and no live claim by another actor. The next round of a repeating task also waits for its planned day. Every surface uses `views.ready`. |
| A02 Cancelled prerequisites | A cancelled or archived dependency releases its dependents | Resolves the question C08 left open. The alternative, requiring an explicit unblock, strands work behind tasks nobody will do. |
| A03 Containers | A task with an open or waiting child task is not ready; a proposed child blocks nothing | Work the leaves. A parent becomes ready when its children are finished; it never completes automatically (C02 stands). |
| A04 Claims | Advisory lease, default 60 minutes, at most seven days | One live holder; the same owner may renew; completion, cancellation and archiving clear it. Not access control. |
| A05 Proposals | `proposed` status with `accept` and `dismiss` | Never ready; cannot be completed, skipped, reopened or moved to another status by update; its reminders stay pending but are not dispatched. Who may accept is set by A18. |
| A06 Next move | Optional `person`, `answer` or `agent`; never inferred | Unset matches no `--move` filter. |
| A07 Notes | Append-only, through the `note` action only | At most 1,000 per entity. Undo keeps notes and the current claim. A new round of a repeating task starts with none. |
| A08 Priority direction | 1 is most important, 4 least, 0 unset | Follows iCalendar. Previously undefined; ordering in `ready` now depends on it. |
| A09 Date input | Fixed grammar; anything else refused | No natural-language interpretation (C03 stands). The phrase used is stored as `expression` beside the resolved value. |
| A10 Readable identifiers | Short `dm-xxxx` IDs from the plain verbs; `series.N` for rounds of a repeating task | A round falls back to the hashed identity when `series.N` is or was used by an unrelated entity, or exceeds the ID limit; a slot that was ever materialized is never materialized again. Databases written before this change keep their hashed successors. |
| A11 Plain verbs and versions | The `Tasks` layer reads the current version and retries stale reads | The engine contract (C10) is unchanged: every write still carries an operation ID and expected version. A repeated `op` in the plain layer replays by ID alone, without comparing the request. |
| A12 Per-write validation | Each write validates its own references through derived lookup tables | Replaces whole-workspace validation on every write; snapshot import still validates the whole workspace. A randomized test checks the two agree. The 100,000-entity bound now applies to import only. |
| A13 Journal mode | Path-owned stores use WAL with default synchronous settings | One durable sync per commit. Caller-owned connections keep their own mode. Creates `-wal` and `-shm` files beside the database. |
| A14 Stored-shape upgrades | A schema marker triggers an in-place rewrite of stored entities when fields are added | Versions and event history are untouched. Snapshots exported by this version contain fields that 0.1.0 rejects. |
| A15 Error text | Domain failures print their stable code and rule; validation names fields without values | Other failures keep the generic message so stored content cannot reach logs. |
| A16 Database location | `--db`, `DAYMARK_DB`, nearest `.daymark/`, legacy `./daymark.sqlite3`, then the per-user database | The plain verbs refuse to run without a database and name `daymark init`. The engine commands keep creating `./daymark.sqlite3` as before. |

| A17 Actor on events | `execute(..., actor=)` is recorded on every event it causes | Scheduler and worker events have no actor. The HTTP service records the token's principal and overrides claim owner and note author with it. Snapshots carry the actor; events imported without one read as unattributed. Operation receipts do not include the actor. |
| A18 Decider policy | A workspace may name who can `accept` or `dismiss`; empty means any writer | Stored beside scheduler state, set locally (`daymark policy`), not exported in snapshots and not settable over HTTP. Enforced against whatever actor name the caller supplies: authenticated over HTTP, self-declared locally. It does not stop an actor creating open tasks directly. |
| A19 Private files | A database the command line creates is mode 0600 | Applies to every command, including the engine commands' legacy `./daymark.sqlite3`. |

| A20 Focus | Optional `focus` rank: the person's own short list, lowest first | Leads `ready` ahead of every computed order. Set only at the person's request; cleared when a task is completed, cancelled or archived. Ties fall back to the usual order; ranks need not be contiguous. |
| A21 Scope | Optional `scope` slug, a filter within a workspace | `ready`, `agenda` and `summary` accept a scope, or the empty string for unscoped items. It is never a boundary and is never inferred. |
| A22 Workspace isolation, stated | A workspace is the only boundary inside a database | Reads, references, history, exports, remembered facts and policy never cross. `Store.workspaces()` and `daymark workspaces` list them for local callers; the HTTP service exposes only the caller's own. |

## Decisions for managed projects

Decision date: 2026-11-13. Chosen by the implementing agent under the
commissioner's delegated routine-decision authority, open to reversal. They make
a project something a board, gantt or table elsewhere can show and edit, with
Daymark as the system of record. C08 and C11
stand: this is a stored model with a read and write surface, not a scheduler,
and the adapter promises no third-party round trip beyond its own fields.

| Decision | Default chosen | Contract and limit |
|---|---|---|
| P01 Stages | A project may define up to 30 ordered stages `{id, label, status, color?}`; a task has an optional `stage` | Lifecycle status stays the truth for readiness; the stage is only where the card sits. A defined set needs at least one open and one completed stage. Colours come from a fixed thirteen-name vocabulary. |
| P02 Default board | A project with no stages reads as To do (open), Waiting (waiting), Done (completed) | Derived, not stored: a task with no stored stage reads in the first stage for its status, so existing projects read as boards with no rewrite. |
| P03 Stage and status, both ways | Moving to a stage sets the task's status to the stage's; a status change by any verb clears a stored stage that no longer fits | Complete, cancel, skip, reopen, wait, resume, accept, dismiss and undo all land the card in its status's first stage. A waiting task may stay in an open stage when the board has no waiting stage. A cancelled task with no cancelled stage is off the board. A proposal sits in no stage and is listed apart: it is not work yet. A new round of a repeating task starts with no stage. |
| P04 Changing stages and projects | Removing a stage, or changing its status, clears the stage of the tasks it no longer fits | Their status never changes; each such task gets a version and a `stage.reset` event in the same transaction. Undoing the project's change restores its stages, not those tasks. A task that moves to another project loses its stage and rank unless the same write sets them. |
| P05 Manual order | A per-task `rank`, a base-62 fractional-index key compared as text, set by the `place` action (`after` or `before` a neighbour, or `null` for first or last) | One reorder is one write of one task, computed inside the write transaction, so concurrent placements serialize. Unranked tasks follow ranked ones, oldest first, so new tasks go last. The first placement in a project with unranked tasks ranks them once, in their current order, which moves their versions. Neighbours must be unarchived tasks in the same project and workspace. |
| P06 Milestones | A `milestone` flag on a task, dated by its own temporal fields | No separate kind and no date of its own. |
| P07 Plan dates | start = `planned`, else `earliest_start`; end = `latest_target`, else `deadline`; the deadline is also given on its own | A plan write sets `planned` and `latest_target` only, so dragging never moves a hard deadline. A write that would put the start after the end, or `latest_target` before `earliest_start`, is refused, never clamped. |
| P08 Plan read and project feed | `Store.plan` reads a project, its stages, tasks in order with resolved stage and dates, in-project dependencies (and a note of outside ones), the workspace's people and labels, roll-ups and the head cursor, in one transaction | `Store.changes(project, after)` answers whether the project, a task in it, a task entering or leaving it, or a person or label changed after a cursor. A cursor ahead of the head reads as changed. Roll-ups are counts: no effort-weighted progress (C15 stands). |
| P09 Batch writes | `Store.batch`: up to 100 operations in one transaction, each with its own operation ID, expected version and receipt | `atomic=True` (default) writes all or nothing and reports the rest `aborted`; `atomic=False` rolls back only the refused ones. Refusals are results, not exceptions; HTTP answers 200 with them. A replay replays each operation by its ID. |
| P10 Externally owned projects | An optional `managed_by` on a project names the system that rewrites it | Advisory: Daymark accepts writes as before; applications that edit plans should treat the project as read-only. Never inferred. |
| P11 Assigning by name | `--assignee` takes a person's ID or exact name, ignoring case | An unknown name is refused, not created; several matches are refused with their IDs. Workspace isolation (A22) is unchanged. |

These choices deliberately avoid parity claims with any surveyed product. A future
feature should reopen its linked decision with a concrete use case, migration
impact and a regression test, rather than inherit a vendor's label without its
meaning.

## Harness adaptation

The [implementation review](implementation-review.md) inspects pinned upstream
source and test harnesses without executing or copying them. For this release,
borrow the independent reference-model technique through bounded deterministic
operation histories (`tests/test_state_sequences.py`): five seeded histories,
forty command opportunities each, two isolated workspaces, deliberate stale
writes, replay, restart and native snapshot transfer. The reference model tracks
only expected title/status/version and cannot stand in for the implementation's
own database logic. Barrier/lease and subprocess-crash regressions remain separate.

Adding a property-testing runtime is deferred; a new dependency is unnecessary to
exercise this bounded contract. A useful future generated trace should become a
small explicit regression before broadening the trial. Neither a bounded run nor
an inspected upstream test is a proof of all interleavings or deployment behavior.
